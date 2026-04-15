package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

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
		Files: []models.FileEntry{
			{Path: "/a.txt", Filename: "a.txt", SizeBytes: 100},
		},
	}

	err := c.SendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}

	if len(received.Files) != 1 {
		t.Errorf("expected 1 file, got %d", len(received.Files))
	}
}
