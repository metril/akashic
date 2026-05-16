package client

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestClient_SendBatch(t *testing.T) {
	var received models.ScanBatch

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/ingest/batch" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-key" {
			t.Error("missing or wrong auth header")
		}
		json.NewDecoder(r.Body).Decode(&received)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
	defer server.Close()

	c := New(server.URL, "test-key")
	batch := models.ScanBatch{
		SourceID: "src-1",
		ScanID:   "scan-1",
		Entries: []models.EntryRecord{
			{Path: "/a.txt", Name: "a.txt", Kind: "file"},
		},
	}

	_, err := c.SendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}

	if len(received.Entries) != 1 {
		t.Errorf("expected 1 entry, got %d", len(received.Entries))
	}
}

// SendBatch retries on 5xx and ultimately succeeds when the API
// recovers. Pre-fix, a single transient 503 killed the scan.
func TestClient_SendBatch_RetriesOn5xx(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		if n < 3 {
			http.Error(w, "transient", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	c := New(server.URL, "k")
	c.backoffBase = 1 * time.Millisecond // keep test fast

	if _, err := c.SendBatch(context.Background(), models.ScanBatch{}); err != nil {
		t.Fatalf("expected success after retry, got %v", err)
	}
	if got := calls.Load(); got != 3 {
		t.Errorf("expected 3 attempts (2 503s + 1 200), got %d", got)
	}
}

// 4xx responses are terminal — retrying a malformed request just
// wastes round-trips and amplifies the api's error log.
func TestClient_SendBatch_DoesNotRetry4xx(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		http.Error(w, "bad", http.StatusBadRequest)
	}))
	defer server.Close()

	c := New(server.URL, "k")
	c.backoffBase = 1 * time.Millisecond

	if _, err := c.SendBatch(context.Background(), models.ScanBatch{}); err == nil {
		t.Fatal("expected error on 400")
	}
	if got := calls.Load(); got != 1 {
		t.Errorf("expected exactly 1 attempt on 400, got %d", got)
	}
}

// decodeBatch reads a possibly gzip-encoded request body into a
// ScanBatch — split tests send batches on both sides of the gzip
// threshold, so the fake API must handle either encoding.
func decodeBatch(t *testing.T, r *http.Request) models.ScanBatch {
	t.Helper()
	var src io.Reader = r.Body
	if r.Header.Get("Content-Encoding") == "gzip" {
		gr, err := gzip.NewReader(r.Body)
		if err != nil {
			t.Errorf("server gzip.NewReader: %v", err)
			return models.ScanBatch{}
		}
		defer gr.Close()
		src = gr
	}
	var b models.ScanBatch
	if err := json.NewDecoder(src).Decode(&b); err != nil {
		t.Errorf("server decode batch: %v", err)
	}
	return b
}

// v0.30.1 — a 413 must NOT kill the scan. SendBatch splits the batch
// and ships the halves; every entry still reaches the API, exactly
// one batch carries is_final, and the response is flagged PayloadSplit.
func TestClient_SendBatch_SplitsOn413(t *testing.T) {
	var (
		mu       sync.Mutex
		gotPaths []string
		finals   int
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := decodeBatch(t, r)
		// Reject anything over 2 entries — stand-in for a reverse
		// proxy with a small client_max_body_size.
		if len(b.Entries) > 2 {
			http.Error(w, "too big", http.StatusRequestEntityTooLarge)
			return
		}
		mu.Lock()
		for _, e := range b.Entries {
			gotPaths = append(gotPaths, e.Path)
		}
		if b.IsFinal {
			finals++
		}
		mu.Unlock()
		cands := make([]models.ExtractCandidate, 0, len(b.Entries))
		for _, e := range b.Entries {
			cands = append(cands, models.ExtractCandidate{Path: e.Path})
		}
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(models.BatchResponse{ExtractCandidates: cands})
	}))
	defer srv.Close()

	c := New(srv.URL, "k")
	c.backoffBase = 1 * time.Millisecond

	entries := make([]models.EntryRecord, 8)
	for i := range entries {
		entries[i] = models.EntryRecord{Path: fmt.Sprintf("/f%d.txt", i), Kind: "file"}
	}
	resp, err := c.SendBatch(context.Background(), models.ScanBatch{
		SourceID: "s", ScanID: "sc", Entries: entries, IsFinal: true,
	})
	if err != nil {
		t.Fatalf("SendBatch should recover from 413, got %v", err)
	}
	if !resp.PayloadSplit {
		t.Error("PayloadSplit should be set after a split")
	}
	if len(resp.ExtractCandidates) != 8 {
		t.Errorf("merged extract candidates: got %d, want 8", len(resp.ExtractCandidates))
	}
	mu.Lock()
	defer mu.Unlock()
	if len(gotPaths) != 8 {
		t.Errorf("delivered entries: got %d, want 8", len(gotPaths))
	}
	if finals != 1 {
		t.Errorf("exactly one batch must carry is_final, got %d", finals)
	}
}

// A single entry that still 413s is dropped — one pathological file
// must never fail the scan. A final batch still terminates cleanly:
// the empty is_final batch gets through.
func TestClient_SendBatch_DropsUnsplittableEntry(t *testing.T) {
	var sawFinal atomic.Bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b := decodeBatch(t, r)
		if len(b.Entries) > 0 {
			http.Error(w, "too big", http.StatusRequestEntityTooLarge)
			return
		}
		if b.IsFinal {
			sawFinal.Store(true)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "k")
	c.backoffBase = 1 * time.Millisecond
	resp, err := c.SendBatch(context.Background(), models.ScanBatch{
		SourceID: "s", ScanID: "sc",
		Entries: []models.EntryRecord{{Path: "/huge.txt", Kind: "file"}},
		IsFinal: true,
	})
	if err != nil {
		t.Fatalf("a single oversized entry must be dropped, not fail the scan: %v", err)
	}
	if !resp.PayloadSplit {
		t.Error("PayloadSplit should be set")
	}
	if !sawFinal.Load() {
		t.Error("the empty is_final batch must still be delivered so the scan terminates")
	}
}
