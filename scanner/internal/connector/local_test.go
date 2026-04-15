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

	var entries []*models.FileEntry
	err := c.Walk(context.Background(), dir, nil, true, func(e *models.FileEntry) error {
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	fileCount := 0
	for _, e := range entries {
		if !e.IsDir {
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
