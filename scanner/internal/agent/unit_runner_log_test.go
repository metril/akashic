// A unit-coordinated scan must stream its per-unit walk log lines to
// the API's /api/scans/{id}/log endpoint (v0.31.6).
//
// Pre-fix runUnitWalk built scanner.Options without the Reporter, so
// scanner.Run logged to local stdout instead of the API — and a scan
// with max_parallel_scanners > 1 showed nothing in the Live Log, stuck
// "waiting for output".
package agent

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/observe"
)

func TestRunUnitWalk_StreamsUnitLogToAPI(t *testing.T) {
	// A subtree for the non-root unit to walk.
	dir := t.TempDir()
	sub := filepath.Join(dir, "sub")
	if err := os.MkdirAll(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, n := range []string{"a.txt", "b.txt"} {
		if err := os.WriteFile(filepath.Join(sub, n), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Count POSTs to the scan-log endpoint — the reporter only posts it
	// when the log sink has buffered lines, so >=1 proves logs flowed.
	var logPosts atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/scans/scan-1/log" {
			logPosts.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		// ingest/batch, heartbeat, etc. — accept with a minimal body.
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"files_processed":0,"scan_id":"scan-1","extract_candidates":[]}`))
	}))
	defer srv.Close()

	state := observe.NewState()
	reporter := observe.New(srv.URL, "key", "scan-1", state)
	reporter.Start(context.Background())

	var conn connector.Connector = connector.NewLocalConnector()
	shallow, ok := conn.(connector.ShallowWalker)
	if !ok {
		t.Fatal("local connector should implement ShallowWalker")
	}

	leased := &leasedScan{
		ScanID:   "scan-1",
		ScanType: "incremental",
		Source:   leasedSource{ID: "src-1", Type: "local"},
	}
	unit := &workUnit{ID: "unit-1", ScanID: "scan-1", Path: "sub"}

	err := runUnitWalk(
		context.Background(), client.New(srv.URL, "key"), conn, shallow,
		leased, dir, unit, state, reporter,
		nil /*excludes*/, nil /*extractor*/, nil /*extractFactory*/, 0,
	)
	if err != nil {
		t.Fatalf("runUnitWalk: %v", err)
	}

	// Stop drains the log sink synchronously before returning.
	reporter.Stop()

	if logPosts.Load() == 0 {
		t.Error("unit walk streamed no log lines to /api/scans/{id}/log — " +
			"Reporter not wired into the unit's scanner.Options")
	}
}
