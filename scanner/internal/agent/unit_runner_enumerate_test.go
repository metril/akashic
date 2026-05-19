// Enumeration-probe coverage for the unit-coordinated scan runner
// (v0.32.1, updated v0.34.0).
//
// `ensureUnitsEnumerated` leases a unit to probe whether the work queue
// exists. A leased probe unit MUST be returned to the caller (abandoning
// it was the v0.32.1 stall); a 204 means this scanner is first and
// enqueues the single root unit; a 409 cap is non-fatal and must not
// re-enumerate.
package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func TestEnsureUnitsEnumerated_ReturnsProbeLeasedUnit(t *testing.T) {
	// A scanner joining a scan whose queue already exists gets a unit
	// leased to it by the probe. That unit MUST be returned so the
	// caller processes (and ultimately /complete-s) it.
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

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv, "scan-1")
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

func TestEnsureUnitsEnumerated_EnqueuesRootUnitWhenQueueEmpty(t *testing.T) {
	// 204 from the probe → this scanner is first; it enqueues the single
	// root unit (path "") via one /work/split call.
	var splitHits atomic.Int32
	var splitPaths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/scans/scan-1/work/lease":
			w.WriteHeader(http.StatusNoContent)
		case "/api/scans/scan-1/work/split":
			splitHits.Add(1)
			var body struct {
				ChildPaths []string `json:"child_paths"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			splitPaths = body.ChildPaths
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"created":1,"skipped":0}`))
		default:
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := Config{APIBase: srv.URL, ScannerID: "scanner-1"}

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv, "scan-1")
	if err != nil {
		t.Fatalf("ensureUnitsEnumerated: %v", err)
	}
	if unit != nil {
		t.Errorf("first scanner enqueues and returns no unit, got %q", unit.ID)
	}
	if splitHits.Load() != 1 {
		t.Fatalf("expected exactly one /work/split call, got %d", splitHits.Load())
	}
	if len(splitPaths) != 1 || splitPaths[0] != "" {
		t.Errorf("first scanner should enqueue just the root unit [\"\"], got %v", splitPaths)
	}
}

func TestEnsureUnitsEnumerated_SkipsEnumerationWhenCapped(t *testing.T) {
	// A 409 from the probe means units already exist but there's no slot
	// for this scanner yet. That must NOT be fatal and must NOT trigger
	// a re-enumeration.
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

	unit, err := ensureUnitsEnumerated(
		context.Background(), srv.Client(), cfg, priv, "scan-1")
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
