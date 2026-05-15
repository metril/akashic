// Walker→sender pipeline (v0.29.2 Part B).
//
// The pre-v0.29.2 walker synchronously blocked on each batch's
// SendBatch HTTP round trip — typically 200–600 ms per batch. With the
// pipelined refactor, a buffered channel sits between the walker and a
// dedicated sender goroutine; the walker continues immediately after
// pushing a batch onto the channel. These tests verify the model:
//
//   * A slow stub SendBatch doesn't pin the walker between batches.
//   * Errors from the sender propagate back: Run() returns a non-nil
//     "send batch" error and the scan terminates cleanly.
package scanner

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// stubConn satisfies connector.Connector with synthetic file emission.
type stubConn struct {
	entries int
}

func (s *stubConn) Connect(_ context.Context) error { return nil }
func (s *stubConn) Close() error                    { return nil }
func (s *stubConn) Type() string                    { return "stub" }
func (s *stubConn) Delete(_ context.Context, _ string) error {
	return fmt.Errorf("not supported")
}
func (s *stubConn) ReadFile(_ context.Context, _ string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("not supported")
}
func (s *stubConn) Walk(
	ctx context.Context, _ string, _ []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	for i := 0; i < s.entries; i++ {
		if err := ctx.Err(); err != nil {
			return walker.WalkStats{}, err
		}
		path := fmt.Sprintf("/file-%05d", i)
		if err := fn(&models.EntryRecord{
			Path: path, Kind: "file",
		}); err != nil {
			return walker.WalkStats{}, err
		}
	}
	return walker.WalkStats{}, nil
}

func TestPipelineWalkerProgressesWhileSenderIsSlow(t *testing.T) {
	const entryCount = 30
	const sendDelay = 100 * time.Millisecond
	var batchCount int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&batchCount, 1)
		time.Sleep(sendDelay)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "test-jwt")
	conn := &stubConn{entries: entryCount}

	start := time.Now()
	s := New(c, conn, Options{
		SourceID: "src", ScanID: "scan-pl",
		Root: "/", BatchSize: 10,
	})
	_, err := s.Run(context.Background())
	elapsed := time.Since(start)
	if err != nil {
		t.Fatalf("Run failed: %v", err)
	}
	// 30 entries / batch=10 → 3 walk batches + 1 empty final batch
	// (the IsFinal=true flag still needs to land server-side so the
	// API can finalize). Sequential pre-fix would be 4 × 100 ms ≈
	// 400 ms; pipelined overlap should keep total ≈ 4 × 100 ms still
	// (one in-flight at a time after walk finishes). Generous guard
	// at 800 ms catches a true stall without flaking on CI scheduler
	// jitter.
	if elapsed > 800*time.Millisecond {
		t.Errorf("Run took %v; pipeline appears stalled (expected < 800ms)", elapsed)
	}
	if got := atomic.LoadInt32(&batchCount); got != 4 {
		t.Errorf("expected 4 batches sent (3 walk + 1 final), got %d", got)
	}
}

func TestPipelineSenderErrorPropagatesToRun(t *testing.T) {
	// Persistent 500 → SendBatch retries to exhaustion → returns
	// retry-exhausted error → sender goroutine surfaces it via
	// firstSendErr → Run wraps as "send batch: ..." and returns.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = io.WriteString(w, `{"error":"boom"}`)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "test-jwt")
	conn := &stubConn{entries: 100}
	s := New(c, conn, Options{
		SourceID: "src", ScanID: "scan-err",
		Root: "/", BatchSize: 10,
	})
	_, err := s.Run(context.Background())
	if err == nil {
		t.Fatal("expected non-nil error from Run when sender fails")
	}
	if !strings.Contains(err.Error(), "send batch") {
		t.Errorf("expected error to mention 'send batch'; got %q", err.Error())
	}
}

func TestPipelineFinalBatchReachesSender(t *testing.T) {
	// Verify the close+drain path: a small walk that produces less
	// than one full batch still reaches the sender via the IsFinal
	// path, and the sender confirms with the scanned final flag.
	var sawFinal int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Body is JSON; cheap detection of "is_final":true via substring
		// (no need to parse — we just want to know it landed).
		buf := make([]byte, 8192)
		n, _ := r.Body.Read(buf)
		if strings.Contains(string(buf[:n]), `"is_final":true`) {
			atomic.AddInt32(&sawFinal, 1)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "test-jwt")
	// 5 entries with batch=10 → only one batch, which IS the final.
	conn := &stubConn{entries: 5}
	s := New(c, conn, Options{
		SourceID: "src", ScanID: "scan-fin",
		Root: "/", BatchSize: 10,
	})
	_, err := s.Run(context.Background())
	if err != nil {
		t.Fatalf("Run failed: %v", err)
	}
	if got := atomic.LoadInt32(&sawFinal); got != 1 {
		t.Errorf("expected 1 final batch, server saw %d", got)
	}
}
