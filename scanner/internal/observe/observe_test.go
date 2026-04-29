package observe

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeAPI captures heartbeat/log/stderr POSTs so tests can assert against
// what the Reporter actually sent.
type fakeAPI struct {
	mu          sync.Mutex
	heartbeats  []map[string]any
	logBatches  [][]map[string]any
	stderrBatch [][]map[string]any
	hits        atomic.Int64
}

func newFakeAPI() *fakeAPI { return &fakeAPI{} }

func (f *fakeAPI) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/scans/", func(w http.ResponseWriter, r *http.Request) {
		f.hits.Add(1)
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/heartbeat"):
			var v map[string]any
			_ = json.Unmarshal(body, &v)
			f.mu.Lock()
			f.heartbeats = append(f.heartbeats, v)
			f.mu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/log"):
			var v struct {
				Lines []map[string]any `json:"lines"`
			}
			_ = json.Unmarshal(body, &v)
			f.mu.Lock()
			f.logBatches = append(f.logBatches, v.Lines)
			f.mu.Unlock()
		case strings.HasSuffix(r.URL.Path, "/stderr"):
			var v struct {
				Chunks []map[string]any `json:"chunks"`
			}
			_ = json.Unmarshal(body, &v)
			f.mu.Lock()
			f.stderrBatch = append(f.stderrBatch, v.Chunks)
			f.mu.Unlock()
		}
		w.WriteHeader(http.StatusNoContent)
	})
	return mux
}

func TestHeartbeatPostsSnapshot(t *testing.T) {
	api := newFakeAPI()
	srv := httptest.NewServer(api.handler())
	defer srv.Close()

	state := NewState()
	state.IncFile()
	state.IncFile()
	state.AddBytes(100)
	state.SetCurrent("/foo/bar", "walk")
	state.SetTotalEstimated(42)

	r := New(srv.URL, "tok", "scan-uuid", state)
	ctx, cancel := context.WithCancel(context.Background())
	r.Start(ctx)

	// Wait for at least one heartbeat. Interval is 1 s; allow generous slack.
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		api.mu.Lock()
		n := len(api.heartbeats)
		api.mu.Unlock()
		if n > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	cancel()
	r.Stop()

	api.mu.Lock()
	defer api.mu.Unlock()
	if len(api.heartbeats) == 0 {
		t.Fatal("expected at least one heartbeat POST")
	}
	hb := api.heartbeats[0]
	if hb["files_scanned"].(float64) != 2 {
		t.Errorf("files_scanned: got %v, want 2", hb["files_scanned"])
	}
	if hb["bytes_scanned"].(float64) != 100 {
		t.Errorf("bytes_scanned: got %v, want 100", hb["bytes_scanned"])
	}
	if hb["current_path"] != "/foo/bar" {
		t.Errorf("current_path: got %v, want /foo/bar", hb["current_path"])
	}
	if hb["phase"] != "walk" {
		t.Errorf("phase: got %v, want walk", hb["phase"])
	}
	if hb["total_estimated"].(float64) != 42 {
		t.Errorf("total_estimated: got %v, want 42", hb["total_estimated"])
	}
}

func TestLogSinkBatchesByCount(t *testing.T) {
	api := newFakeAPI()
	srv := httptest.NewServer(api.handler())
	defer srv.Close()

	r := New(srv.URL, "tok", "scan-uuid", NewState())
	ctx, cancel := context.WithCancel(context.Background())
	r.Start(ctx)

	// Push exactly 10 lines (the batch threshold) — should flush as one
	// batch on the size trigger, not the timer.
	for i := 0; i < 10; i++ {
		r.LogSink().Info("msg %d", i)
	}

	// Allow the drain goroutine to handle the flush.
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		api.mu.Lock()
		n := len(api.logBatches)
		api.mu.Unlock()
		if n >= 1 {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	cancel()
	r.Stop()

	api.mu.Lock()
	defer api.mu.Unlock()
	if len(api.logBatches) == 0 {
		t.Fatalf("expected at least one log batch")
	}
	totalLines := 0
	for _, b := range api.logBatches {
		totalLines += len(b)
	}
	if totalLines != 10 {
		t.Errorf("total lines posted: got %d, want 10", totalLines)
	}
	first := api.logBatches[0][0]
	if first["level"] != "info" {
		t.Errorf("first level: got %v, want info", first["level"])
	}
}

func TestLogSinkDropsWhenFullAndReportsOverflow(t *testing.T) {
	api := newFakeAPI()
	srv := httptest.NewServer(api.handler())
	defer srv.Close()

	r := New(srv.URL, "tok", "scan-uuid", NewState())
	// Don't Start the reporter: with no drain goroutine, the channel
	// fills and we exercise the drop path. This test verifies emit() is
	// non-blocking even when nothing is reading.
	target := logBufferCap + 50
	for i := 0; i < target; i++ {
		r.LogSink().Info("flood %d", i)
	}
	dropped := r.LogSink().takeDropped()
	if dropped < 50 {
		t.Errorf("dropped count: got %d, want >= 50", dropped)
	}
}
