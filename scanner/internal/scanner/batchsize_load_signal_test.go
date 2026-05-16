// AdaptiveBatcher only halves on real load signals (v0.29.6).
//
// AIMD's multiplicative-decrease on error was previously triggered by
// ANY non-nil error. v0.29.6 narrows that to "this error indicates
// real load" — 5xx or network failure. The Observe contract hasn't
// changed; the SENDER passes nil for non-load errors so the AIMD
// primitive itself stays simple (it still halves on any non-nil err
// it sees; the policy lives one layer up).
//
// A 413 is no longer in scope here: as of v0.30.1 SendBatch recovers
// from a 413 by splitting the batch, so it never surfaces as an error
// to the sender — the shrink is driven by NotePayloadTooLarge
// instead (covered in batchsize_test.go).
//
// These tests verify the layer-up policy via the same client.IsLoadSignal
// helper the sender uses.
package scanner

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestAdaptiveBatcher_HalvesOnly_OnLoadSignal(t *testing.T) {
	cases := []struct {
		name       string
		status     int
		wantHalved bool
	}{
		{"500 halves", 500, true},
		{"503 halves", 503, true},
		{"422 stays", 422, false},
		{"400 stays", 400, false},
		{"401 stays", 401, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var hits int32
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				atomic.AddInt32(&hits, 1)
				w.WriteHeader(tc.status)
			}))
			defer srv.Close()
			c := client.New(srv.URL, "k")
			_, err := c.SendBatch(context.Background(), models.ScanBatch{
				SourceID: "s", ScanID: "sc",
				Entries: []models.EntryRecord{{Path: "/a", Kind: "file"}},
			})
			if err == nil {
				t.Fatal("expected error from SendBatch")
			}

			// Mirror what the sender goroutine does in scanner.go:
			// SKIP Observe entirely on non-load errors. Calling
			// Observe with err=nil + a fast latency would grow the
			// batch (additive increase), which is wrong for a 422.
			ab := NewAdaptiveBatchSize(1000, 250, 5000, 100, 400)
			if err == nil || client.IsLoadSignal(err) {
				ab.Observe(50*time.Millisecond, err)
			}
			got := ab.Current()
			if tc.wantHalved && got != 500 {
				t.Errorf("status %d: expected halve 1000→500 on load signal; got %d",
					tc.status, got)
			}
			if !tc.wantHalved && got != 1000 {
				t.Errorf("status %d: expected size unchanged (non-load 4xx); got %d",
					tc.status, got)
			}
		})
	}
}

func TestAdaptiveBatcher_PlainErrStillHalves(t *testing.T) {
	// Inside Observe itself a non-nil error still halves. The
	// policy that turns 4xx into nil lives in the SENDER. This test
	// confirms the primitive's contract is unchanged.
	ab := NewAdaptiveBatchSize(1000, 250, 5000, 100, 400)
	ab.Observe(50*time.Millisecond, errors.New("boom"))
	if ab.Current() != 500 {
		t.Errorf("Observe on plain non-nil err should halve; got %d", ab.Current())
	}
}
