package scanner

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func newTestServer(t *testing.T, onBatch func(models.ScanBatch)) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if onBatch != nil {
			var b models.ScanBatch
			if err := json.NewDecoder(r.Body).Decode(&b); err == nil {
				onBatch(b)
			}
		}
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
}

func TestScanner_ScanLocal(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "f1.txt"), []byte("one"), 0644)
	os.WriteFile(filepath.Join(dir, "f2.txt"), []byte("two"), 0644)
	os.MkdirAll(filepath.Join(dir, "sub"), 0755)
	os.WriteFile(filepath.Join(dir, "sub", "f3.txt"), []byte("three"), 0644)

	var batchCount atomic.Int32
	var sawDirectory atomic.Bool
	server := newTestServer(t, func(b models.ScanBatch) {
		batchCount.Add(1)
		for _, e := range b.Entries {
			if e.Kind == "directory" {
				sawDirectory.Store(true)
			}
		}
	})
	defer server.Close()

	apiClient := client.New(server.URL, "key")
	conn := connector.NewLocalConnector()

	s := New(apiClient, conn, Options{
		SourceID:  "test-source",
		ScanID:    "test-scan",
		Root:      dir,
		BatchSize: 2,
		Hash:      true,
	})

	result, err := s.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if result.FilesFound < 3 {
		t.Errorf("expected at least 3 files, got %d", result.FilesFound)
	}
	if result.DirsFound == 0 {
		t.Error("expected directory entries to be reported")
	}
	if !sawDirectory.Load() {
		t.Error("expected at least one directory entry in the batches")
	}

	if batchCount.Load() < 2 {
		t.Errorf("expected at least 2 batches with batch size 2, got %d", batchCount.Load())
	}
}

func TestScanner_Incremental_PastLastScan(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("alpha"), 0644)
	os.WriteFile(filepath.Join(dir, "b.txt"), []byte("beta"), 0644)

	var received []models.EntryRecord
	server := newTestServer(t, func(b models.ScanBatch) {
		received = append(received, b.Entries...)
	})
	defer server.Close()

	past := time.Now().Add(-24 * time.Hour)
	s := New(client.New(server.URL, "key"), connector.NewLocalConnector(), Options{
		SourceID:     "src",
		ScanID:       "scan",
		Root:         dir,
		BatchSize:    100,
		Hash:         true,
		LastScanTime: &past,
	})

	result, err := s.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if result.FilesFound != 2 {
		t.Fatalf("expected 2 files, got %d", result.FilesFound)
	}

	for _, entry := range received {
		if entry.IsDir() {
			continue
		}
		if entry.ContentHash == "" {
			t.Errorf("file %s: expected non-empty hash (mtime after last scan), got empty", entry.Path)
		}
	}
}

func TestScanner_Incremental_FutureLastScan(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("alpha"), 0644)
	os.WriteFile(filepath.Join(dir, "b.txt"), []byte("beta"), 0644)

	var received []models.EntryRecord
	server := newTestServer(t, func(b models.ScanBatch) {
		received = append(received, b.Entries...)
	})
	defer server.Close()

	future := time.Now().Add(24 * time.Hour)
	s := New(client.New(server.URL, "key"), connector.NewLocalConnector(), Options{
		SourceID:     "src",
		ScanID:       "scan",
		Root:         dir,
		BatchSize:    100,
		Hash:         true,
		LastScanTime: &future,
	})

	result, err := s.Run(context.Background())
	if err != nil {
		t.Fatal(err)
	}

	if result.FilesFound != 2 {
		t.Fatalf("expected 2 files, got %d", result.FilesFound)
	}

	for _, entry := range received {
		if entry.IsDir() {
			continue
		}
		if entry.ContentHash != "" {
			t.Errorf("file %s: expected empty hash (mtime before last scan), got %s", entry.Path, entry.ContentHash)
		}
	}
}
