package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
