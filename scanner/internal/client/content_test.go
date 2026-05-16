package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestSendContent_PostsToContentEndpoint(t *testing.T) {
	var sawPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "jwt")
	err := c.SendContent(context.Background(), models.ContentBatch{
		SourceID: "s", ScanID: "sc",
		Items: []models.ContentItem{{Path: "/a.pdf", ContentText: "hello"}},
	})
	if err != nil {
		t.Fatalf("SendContent: %v", err)
	}
	if sawPath != "/api/ingest/content" {
		t.Errorf("posted to %q, want /api/ingest/content", sawPath)
	}
}

func TestSendContent_RetriesOn5xx(t *testing.T) {
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) < 3 {
			http.Error(w, "transient", http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "jwt")
	c.backoffBase = 1 * time.Millisecond
	if err := c.SendContent(context.Background(), models.ContentBatch{}); err != nil {
		t.Fatalf("expected success after retry, got %v", err)
	}
	if got := calls.Load(); got != 3 {
		t.Errorf("attempts: got %d, want 3", got)
	}
}

func TestSendContent_DoesNotRetry4xx(t *testing.T) {
	var calls atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		http.Error(w, "bad", http.StatusBadRequest)
	}))
	defer srv.Close()

	c := New(srv.URL, "jwt")
	c.backoffBase = 1 * time.Millisecond
	if err := c.SendContent(context.Background(), models.ContentBatch{}); err == nil {
		t.Fatal("expected error on 400")
	}
	if got := calls.Load(); got != 1 {
		t.Errorf("4xx must not retry: got %d attempts, want 1", got)
	}
}

// v0.30.1 — a 413 on a content batch is recovered by splitting, not
// dropped wholesale. Every item still reaches the API.
func TestSendContent_SplitsOn413(t *testing.T) {
	var (
		mu    sync.Mutex
		paths []string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b models.ContentBatch
		json.NewDecoder(r.Body).Decode(&b)
		if len(b.Items) > 2 {
			http.Error(w, "too big", http.StatusRequestEntityTooLarge)
			return
		}
		mu.Lock()
		for _, it := range b.Items {
			paths = append(paths, it.Path)
		}
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "jwt")
	c.backoffBase = 1 * time.Millisecond

	items := make([]models.ContentItem, 6)
	for i := range items {
		items[i] = models.ContentItem{Path: fmt.Sprintf("/c%d", i), ContentText: "x"}
	}
	if err := c.SendContent(context.Background(), models.ContentBatch{
		SourceID: "s", ScanID: "sc", Items: items,
	}); err != nil {
		t.Fatalf("SendContent should recover from 413, got %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(paths) != 6 {
		t.Errorf("delivered items: got %d, want 6", len(paths))
	}
}

// A lone content item that still 413s is dropped without error —
// content extraction is best-effort and never fails the scan.
func TestSendContent_DropsUnsplittableItem(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "too big", http.StatusRequestEntityTooLarge)
	}))
	defer srv.Close()

	c := New(srv.URL, "jwt")
	c.backoffBase = 1 * time.Millisecond
	err := c.SendContent(context.Background(), models.ContentBatch{
		SourceID: "s", ScanID: "sc",
		Items: []models.ContentItem{{Path: "/huge.pdf", ContentText: "x"}},
	})
	if err != nil {
		t.Fatalf("a lone oversized item must be dropped, not error: %v", err)
	}
}
