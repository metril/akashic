package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClient_Search(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/search" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		q := r.URL.Query().Get("q")
		if q != "report" {
			t.Errorf("expected query 'report', got '%s'", q)
		}
		json.NewEncoder(w).Encode(map[string]interface{}{
			"results": []map[string]interface{}{
				{"filename": "report.pdf", "path": "/data/report.pdf"},
			},
			"total": 1,
			"query": "report",
		})
	}))
	defer server.Close()

	c := New(server.URL, "test-key")
	results, err := c.Search(context.Background(), "report", nil)
	if err != nil {
		t.Fatal(err)
	}
	if results.Total != 1 {
		t.Errorf("expected 1 result, got %d", results.Total)
	}
}

func TestClient_ListSources(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]interface{}{
			{"id": "1", "name": "nas1", "type": "smb", "status": "online"},
		})
	}))
	defer server.Close()

	c := New(server.URL, "test-key")
	sources, err := c.ListSources(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 1 {
		t.Errorf("expected 1 source, got %d", len(sources))
	}
}

// GetScan + CancelScan are the new endpoints backing `scan wait` and
// `scan cancel`. Smoke-test happy path + 404 → APIError surface so the
// CLI exit-code mapping in commands/scans.go has something to react to.
func TestClient_GetScan(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/scans/abc" {
			json.NewEncoder(w).Encode(map[string]interface{}{
				"id": "abc", "source_id": "src", "status": "completed",
				"files_found": 12, "started_at": "now",
			})
			return
		}
		http.Error(w, "not found", http.StatusNotFound)
	}))
	defer server.Close()

	c := New(server.URL, "k")
	s, err := c.GetScan(context.Background(), "abc")
	if err != nil {
		t.Fatalf("happy path: %v", err)
	}
	if s.Status != "completed" {
		t.Errorf("status: got %q, want completed", s.Status)
	}

	if _, err := c.GetScan(context.Background(), "missing"); err == nil {
		t.Fatal("expected error on 404")
	} else {
		var apiErr *APIError
		if !errors.As(err, &apiErr) || apiErr.Status != 404 {
			t.Errorf("expected APIError status=404, got %T %v", err, err)
		}
	}
}

func TestClient_CancelScan(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		json.NewEncoder(w).Encode(map[string]string{"scan_id": "x", "status": "cancelled"})
	}))
	defer server.Close()
	c := New(server.URL, "k")
	if err := c.CancelScan(context.Background(), "x"); err != nil {
		t.Fatal(err)
	}
	if gotPath != "/api/scans/x/cancel" {
		t.Errorf("expected /api/scans/x/cancel, got %s", gotPath)
	}
}

