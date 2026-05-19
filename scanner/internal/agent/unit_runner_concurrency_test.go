// Per-scanner unit concurrency (v0.35.0).
//
// With MaxConcurrentUnits > 1, runUnitCoordinated spawns that many
// worker goroutines, each with its own connector, all draining the
// one shared work-unit queue. They must drain every unit and exit
// cleanly when the scan goes terminal.
package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestRunUnitCoordinated_WorkersDrainQueueConcurrently(t *testing.T) {
	// A temp tree of 6 small subdirectories, each one work unit. With
	// MaxConcurrentUnits=3, three worker goroutines must drain all six
	// and runUnitCoordinated must return nil once the queue is empty.
	dir := t.TempDir()
	const nUnits = 6
	unitPaths := make([]string, nUnits)
	for i := 0; i < nUnits; i++ {
		name := fmt.Sprintf("sub%d", i)
		unitPaths[i] = name
		sd := filepath.Join(dir, name)
		if err := os.MkdirAll(sd, 0o755); err != nil {
			t.Fatal(err)
		}
		for _, f := range []string{"a.txt", "b.txt"} {
			if err := os.WriteFile(filepath.Join(sd, f), []byte("x"), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}

	var mu sync.Mutex
	queue := append([]string(nil), unitPaths...)
	inflight, maxInflight, leasedCount := 0, 0, 0
	var completes atomic.Int32

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		switch {
		case strings.HasSuffix(path, "/work/lease"):
			mu.Lock()
			if len(queue) == 0 {
				mu.Unlock()
				// Queue drained — the scan is terminal. leaseUnit maps
				// this 409 to errScanTerminal and the worker exits.
				w.WriteHeader(http.StatusConflict)
				_, _ = w.Write([]byte(
					`{"detail":{"status":"completed","reason":"scan-terminal","message":"done"}}`))
				return
			}
			p := queue[0]
			queue = queue[1:]
			leasedCount++
			inflight++
			if inflight > maxInflight {
				maxInflight = inflight
			}
			mu.Unlock()
			// Hold the response briefly so concurrent workers' leases
			// overlap — a serialised (single-worker) run never would.
			time.Sleep(30 * time.Millisecond)
			mu.Lock()
			inflight--
			mu.Unlock()
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(fmt.Sprintf(
				`{"id":%q,"scan_id":"scan-1","path":%q,"status":"running"}`,
				"unit-"+p, p)))
		case strings.HasSuffix(path, "/complete"):
			completes.Add(1)
			w.WriteHeader(http.StatusNoContent)
		case strings.HasSuffix(path, "/fail"):
			t.Errorf("unexpected /fail request: %s", path)
			w.WriteHeader(http.StatusNoContent)
		case strings.HasSuffix(path, "/work/split"):
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"created":0,"skipped":0}`))
		case strings.HasSuffix(path, "/log"):
			w.WriteHeader(http.StatusNoContent)
		default:
			// ingest/batch, heartbeat — accept with a minimal body.
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(
				`{"files_processed":0,"scan_id":"scan-1","extract_candidates":[]}`))
		}
	}))
	defer srv.Close()

	_, priv, _ := ed25519.GenerateKey(rand.Reader)
	cfg := Config{APIBase: srv.URL, ScannerID: "scanner-1", MaxConcurrentUnits: 3}
	leased := &leasedScan{
		ScanID:   "scan-1",
		ScanType: "incremental",
		Source: leasedSource{
			ID:                  "src-1",
			Type:                "local",
			ConnectionConfig:    map[string]any{"path": dir},
			MaxParallelScanners: 4,
		},
	}

	done := make(chan error, 1)
	go func() {
		done <- runUnitCoordinated(context.Background(), srv.Client(), cfg, priv, leased)
	}()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("runUnitCoordinated: %v", err)
		}
	case <-time.After(15 * time.Second):
		t.Fatal("runUnitCoordinated did not return — a worker failed to exit on scan-terminal")
	}

	if got := completes.Load(); got != nUnits {
		t.Errorf("got %d unit completions, want %d — queue not fully drained", got, nUnits)
	}
	mu.Lock()
	gotLeased, gotMax := leasedCount, maxInflight
	mu.Unlock()
	if gotLeased != nUnits {
		t.Errorf("leased %d units, want %d", gotLeased, nUnits)
	}
	if gotMax < 2 {
		t.Errorf("max concurrent in-flight leases was %d; with MaxConcurrentUnits=3 "+
			"the workers did not run in parallel", gotMax)
	}
}

func TestLeasedSource_DecodesScanChunkSize(t *testing.T) {
	// The API resolves scan_chunk_size (source ?? host ?? default) and
	// sends a concrete int; the agent must decode it into ScanChunkSize
	// so runUnitWalk can pass it as the shallow-split budget.
	const body = `{"scan_id":"s","scan_type":"full","api_jwt":"j","source":` +
		`{"id":"src","type":"local","connection_config":{},` +
		`"max_parallel_scanners":3,"scan_chunk_size":500}}`
	var ls leasedScan
	if err := json.Unmarshal([]byte(body), &ls); err != nil {
		t.Fatalf("decode leasedScan: %v", err)
	}
	if ls.Source.ScanChunkSize != 500 {
		t.Errorf("ScanChunkSize = %d, want 500", ls.Source.ScanChunkSize)
	}
	// An older API omits the field — it must decode to 0 so scanner.Run
	// falls back to its own default.
	var ls2 leasedScan
	if err := json.Unmarshal([]byte(
		`{"scan_id":"s","source":{"id":"x","type":"local","max_parallel_scanners":2}}`,
	), &ls2); err != nil {
		t.Fatalf("decode legacy leasedScan: %v", err)
	}
	if ls2.Source.ScanChunkSize != 0 {
		t.Errorf("omitted scan_chunk_size = %d, want 0", ls2.Source.ScanChunkSize)
	}
}
