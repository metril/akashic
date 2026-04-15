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

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
)

func TestScanner_ScanLocal(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "f1.txt"), []byte("one"), 0644)
	os.WriteFile(filepath.Join(dir, "f2.txt"), []byte("two"), 0644)
	os.MkdirAll(filepath.Join(dir, "sub"), 0755)
	os.WriteFile(filepath.Join(dir, "sub", "f3.txt"), []byte("three"), 0644)

	var batchCount atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		batchCount.Add(1)
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	}))
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

	if batchCount.Load() < 2 {
		t.Errorf("expected at least 2 batches with batch size 2, got %d", batchCount.Load())
	}
}
