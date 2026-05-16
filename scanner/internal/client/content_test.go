package client

import (
	"context"
	"net/http"
	"net/http/httptest"
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
