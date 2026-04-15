package client

import (
	"context"
	"encoding/json"
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
