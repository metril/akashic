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
		Entries: []models.EntryRecord{
			{Path: "/a.txt", Name: "a.txt", Kind: "file"},
		},
	}

	err := c.SendBatch(context.Background(), batch)
	if err != nil {
		t.Fatal(err)
	}

	if len(received.Entries) != 1 {
		t.Errorf("expected 1 entry, got %d", len(received.Entries))
	}
}
