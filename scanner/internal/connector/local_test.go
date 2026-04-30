package connector

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestLocalConnector_Walk(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("aaa"), 0644)
	os.MkdirAll(filepath.Join(dir, "sub"), 0755)
	os.WriteFile(filepath.Join(dir, "sub", "b.txt"), []byte("bbb"), 0644)

	c := NewLocalConnector()
	if err := c.Connect(context.Background()); err != nil {
		t.Fatal(err)
	}
	defer c.Close()

	var entries []*models.EntryRecord
	err := c.Walk(context.Background(), dir, nil, true, true, func(e *models.EntryRecord) error {
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	fileCount := 0
	for _, e := range entries {
		if !e.IsDir() {
			fileCount++
			if e.ContentHash == "" {
				t.Errorf("expected hash for %s", e.Path)
			}
		}
	}
	if fileCount != 2 {
		t.Errorf("expected 2 files, got %d", fileCount)
	}
}

func TestLocalConnector_ReadFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "read.txt")
	os.WriteFile(path, []byte("read me"), 0644)

	c := NewLocalConnector()
	c.Connect(context.Background())
	defer c.Close()

	reader, err := c.ReadFile(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	defer reader.Close()

	data, _ := io.ReadAll(reader)
	if string(data) != "read me" {
		t.Errorf("expected 'read me', got '%s'", string(data))
	}
}

func TestLocalConnector_Type(t *testing.T) {
	c := NewLocalConnector()
	if c.Type() != "local" {
		t.Errorf("expected type 'local', got '%s'", c.Type())
	}
}

func TestLocalConnector_Delete(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "victim.txt")
	if err := os.WriteFile(path, []byte("doomed"), 0644); err != nil {
		t.Fatal(err)
	}

	c := NewLocalConnector()
	if err := c.Delete(context.Background(), path); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatalf("file should be gone, got err=%v", err)
	}
}

// Regression: directories must not be deletable. The duplicates flow
// only ever passes file paths, so anything reaching Delete with a dir
// is a bug — fail loudly rather than rmdir.
func TestLocalConnector_DeleteRefusesDirectory(t *testing.T) {
	dir := t.TempDir()
	sub := filepath.Join(dir, "subdir")
	if err := os.Mkdir(sub, 0755); err != nil {
		t.Fatal(err)
	}

	c := NewLocalConnector()
	err := c.Delete(context.Background(), sub)
	if err == nil {
		t.Fatal("expected error when Delete is given a directory")
	}
	if _, statErr := os.Stat(sub); statErr != nil {
		t.Fatalf("directory should still exist, got err=%v", statErr)
	}
}
