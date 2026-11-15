package observe

import (
	"bytes"
	"context"
	"io"
	"os"
	"sync"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

const (
	stderrBatchBytes    = 4096
	stderrBatchInterval = 200 * time.Millisecond
)

// StartStderrRelay replaces os.Stderr with the write end of a pipe; reads
// from the pipe in a goroutine; debounces chunks (4 KB or 200 ms); POSTs
// to /api/v1/scans/{id}/stderr; and tees back to the original stderr so the
// console behaviour for users running the scanner directly is unchanged.
//
// MUST be called early in main() — anything writing to stderr before this
// point bypasses the relay. Returns a cleanup func; defer it.
//
// This is process-wide and irreversible mid-process — DO NOT call from
// tests. Tests should use the LogSink directly, not the stderr relay.
func (r *Reporter) StartStderrRelay(ctx context.Context) (cleanup func(), err error) {
	pr, pw, err := os.Pipe()
	if err != nil {
		return func() {}, err
	}
	originalStderr := os.Stderr
	os.Stderr = pw

	relayCtx, cancel := context.WithCancel(ctx)
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		r.runStderrRelay(relayCtx, pr, originalStderr)
	}()

	cleanup = func() {
		// Closing the write end signals EOF to the reader; the reader
		// flushes any buffered bytes and returns.
		// Do NOT cancel before the reader hits EOF: that would drop the
		// final buffered stderr. cancel() only runs after the drain.
		os.Stderr = originalStderr
		pw.Close()
		wg.Wait()
		cancel()
		pr.Close()
	}
	return cleanup, nil
}

func (r *Reporter) runStderrRelay(ctx context.Context, pr *os.File, tee io.Writer) {
	buf := make([]byte, 8192)
	pending := bytes.Buffer{}
	flushTicker := time.NewTicker(stderrBatchInterval)
	defer flushTicker.Stop()

	flushWith := func(c context.Context) {
		if pending.Len() == 0 {
			return
		}
		// Drain the write to the tee even if the API POST fails — the
		// user running the scanner manually still expects console output.
		if tee != nil {
			tee.Write(pending.Bytes())
		}
		r.postStderrChunk(c, pending.String())
		pending.Reset()
	}
	flush := func() { flushWith(ctx) }
	flushFinal := func() {
		c, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		flushWith(c)
		cancel()
	}

	// Read in a separate goroutine so we can multiplex with the ticker
	// in the main select.
	readCh := make(chan []byte, 4)
	readErr := make(chan error, 1)
	go func() {
		for {
			n, err := pr.Read(buf)
			if n > 0 {
				cp := make([]byte, n)
				copy(cp, buf[:n])
				readCh <- cp
			}
			if err != nil {
				readErr <- err
				close(readCh)
				return
			}
		}
	}()

	for {
		select {
		case <-ctx.Done():
			flushFinal()
			// Keep draining so the reader goroutine never blocks on
			// readCh; console tee only (API ctx is gone). Ends when the
			// write end closes (cleanup) and readCh is closed.
			for data := range readCh {
				if tee != nil {
					tee.Write(data)
				}
			}
			return
		case <-flushTicker.C:
			flush()
		case data, ok := <-readCh:
			if !ok {
				flushFinal()
				return
			}
			pending.Write(data)
			if pending.Len() >= stderrBatchBytes {
				flush()
			}
		case <-readErr:
			// readCh is closed right after readErr; drain what's left.
			for data := range readCh {
				pending.Write(data)
			}
			flushFinal()
			return
		}
	}
}

func (r *Reporter) postStderrChunk(ctx context.Context, chunk string) {
	if chunk == "" {
		return
	}
	resp, err := r.api.PostStderrBatch(ctx, r.scanID, apiclient.StderrBatchIn{
		Chunks: []apiclient.StderrChunkIn{{Ts: time.Now().UTC(), Chunk: chunk}},
	})
	if err != nil {
		return
	}
	resp.Body.Close()
}
