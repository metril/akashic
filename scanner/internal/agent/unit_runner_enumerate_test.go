// Enumeration-probe coverage for the unit-coordinated scan runner
// (v0.32.1).
//
// Pre-fix `ensureUnitsEnumerated` leased a unit purely to probe whether
// the work queue existed, then DELIBERATELY abandoned it (no /complete)
// — relying on the 60 s lease to expire. When a scan finished inside
// that window every scanner exited and nothing re-leased the orphaned
// unit, so the scan never finalized and stalled in `running`.
//
// The fix: the probe-leased unit is returned to the caller, which
// processes it like any other unit. These tests pin that contract —
// a leased probe unit comes back non-nil; a 204 enumerates; a 409 cap
// is non-fatal and does not re-enumerate.
package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/connector"
)

func TestEnsureUnitsEnumerated_ReturnsProbeLeasedUnit(t *testing.T) {
	// A scanner joining a scan whose queue already exists gets a unit
	// leased to it by the probe. That unit MUST be returned so the
	// caller processes (and ultimately /complete-s) it — abandoning it
	// was the v0.32.1 stall.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/scans/scan-1/work/lease" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(
				`{"id":"unit-7","scan_id":"scan-1","path":"sub","status":"running"}`))
			return
		}
		t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	cfg := Config{APIBase: srv.URL, ScannerID: "scanner-1"}
	var conn connector.Connector = connector.NewLocalConnector()
	shallow, ok := conn.(connector.ShallowWalker)
	if !ok {
		t.Fatal("local connector should implement ShallowWalker")
	}

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv,
		"scan-1", t.TempDir(), shallow, nil,
	)
	if err != nil {
		t.Fatalf("ensureUnitsEnumerated: %v", err)
	}
	if unit == nil {
		t.Fatal("probe-leased unit was abandoned (nil returned) — the " +
			"v0.32.1 stall bug")
	}
	if unit.ID != "unit-7" {
		t.Errorf("got unit ID %q, want unit-7", unit.ID)
	}
}

func TestEnsureUnitsEnumerated_EnumeratesWhenQueueEmpty(t *testing.T) {
	// 204 from the probe → this scanner is the first to arrive; it
	// shallow-walks the root and splits the queue.
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "subdir"), 0o755); err != nil {
		t.Fatal(err)
	}
	var splitHits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/scans/scan-1/work/lease":
			w.WriteHeader(http.StatusNoContent)
		case "/api/scans/scan-1/work/split":
			splitHits.Add(1)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"created":2,"skipped":0}`))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := Config{APIBase: srv.URL, ScannerID: "scanner-1"}
	var conn connector.Connector = connector.NewLocalConnector()
	shallow, ok := conn.(connector.ShallowWalker)
	if !ok {
		t.Fatal("local connector should implement ShallowWalker")
	}
	if err := conn.Connect(context.Background()); err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer conn.Close()

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv,
		"scan-1", dir, shallow, nil,
	)
	if err != nil {
		t.Fatalf("ensureUnitsEnumerated: %v", err)
	}
	if unit != nil {
		t.Errorf("first scanner enumerates and returns no unit, got %q", unit.ID)
	}
	if splitHits.Load() != 1 {
		t.Fatalf("expected exactly one /work/split call, got %d", splitHits.Load())
	}
}

func TestEnsureUnitsEnumerated_SkipsEnumerationWhenCapped(t *testing.T) {
	// A 409 from the probe means units already exist but there's no slot
	// for this scanner yet. That must NOT be fatal (pre-fix it aborted
	// the whole scan) and must NOT trigger a re-enumeration.
	var splitHits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/scans/scan-1/work/lease":
			w.WriteHeader(http.StatusConflict)
			_, _ = w.Write([]byte("max_parallel_scanners cap reached"))
		case "/api/scans/scan-1/work/split":
			splitHits.Add(1)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"created":0,"skipped":0}`))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := Config{APIBase: srv.URL, ScannerID: "scanner-1"}
	var conn connector.Connector = connector.NewLocalConnector()
	shallow, ok := conn.(connector.ShallowWalker)
	if !ok {
		t.Fatal("local connector should implement ShallowWalker")
	}

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv,
		"scan-1", t.TempDir(), shallow, nil,
	)
	if err != nil {
		t.Fatalf("a lease cap must not be fatal: %v", err)
	}
	if unit != nil {
		t.Errorf("expected no unit on the capped path, got %q", unit.ID)
	}
	if splitHits.Load() != 0 {
		t.Error("ensureUnitsEnumerated re-enumerated despite the queue " +
			"already existing (409 cap)")
	}
}
