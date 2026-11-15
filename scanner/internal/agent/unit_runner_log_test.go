// A unit-coordinated scan must stream its per-unit walk log lines to
// the API's /api/v1/scans/{id}/log endpoint (v0.31.6).
//
// Pre-fix runUnitWalk built scanner.Options without the Reporter, so
// scanner.Run logged to local stdout instead of the API — and a scan
// with max_parallel_scanners > 1 showed nothing in the Live Log, stuck
// "waiting for output".
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

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
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
		if r.URL.Path == "/api/v1/scans/"+testScanID.String()+"/log" {
			logPosts.Add(1)
			w.WriteHeader(http.StatusNoContent)
			return
		}
		// ingest/batch, heartbeat, etc. — accept with a minimal body.
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"files_processed":0,"scan_id":"55555555-5555-4555-8555-555555555555","extract_candidates":[]}`))
	}))
	defer srv.Close()

	state := observe.NewState()
	reporter, err := observe.New(srv.URL, "key", testScanID, state)
	if err != nil {
		t.Fatal(err)
	}
	reporter.Start(context.Background())

	var conn connector.Connector = connector.NewLocalConnector()
	if _, ok := conn.(connector.ShallowWalker); !ok {
		t.Fatal("local connector should implement ShallowWalker")
	}

	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := Config{APIBase: srv.URL, ScannerID: testScannerID}
	leased := &apiclient.LeasedScan{
		ScanId:   testScanID,
		ScanType: "incremental",
		Source:   apiclient.LeasedSource{Id: testSourceID, Type: "local"},
	}
	unit := &apiclient.WorkUnitOut{Id: testUnitID, ScanId: testScanID, Path: "sub"}

	apiClient, err := client.New(srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	err = runUnitWalk(
		context.Background(), srv.Client(), cfg, priv,
		apiClient, conn,
		leased, dir, unit, state, reporter,
		nil /*excludes*/, nil /*extractor*/, nil /*extractFactory*/, 0,
	)
	if err != nil {
		t.Fatalf("runUnitWalk: %v", err)
	}

	// Stop drains the log sink synchronously before returning.
	reporter.Stop()

	if logPosts.Load() == 0 {
		t.Error("unit walk streamed no log lines to /api/v1/scans/{id}/log — " +
			"Reporter not wired into the unit's scanner.Options")
	}
}
