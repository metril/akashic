package walker

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func setupTestTree(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()

	os.WriteFile(filepath.Join(dir, "file1.txt"), []byte("hello"), 0644)
	os.MkdirAll(filepath.Join(dir, "subdir"), 0755)
	os.WriteFile(filepath.Join(dir, "subdir", "file2.log"), []byte("world"), 0644)
	os.MkdirAll(filepath.Join(dir, "subdir", ".git"), 0755)
	os.WriteFile(filepath.Join(dir, "subdir", ".git", "config"), []byte("gitcfg"), 0644)

	return dir
}

func TestWalk_AllFiles(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.FileEntry
	err := Walk(dir, nil, false, func(entry *models.FileEntry) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	if len(entries) < 3 {
		t.Errorf("expected at least 3 entries, got %d", len(entries))
	}
}

func TestWalk_ExcludePatterns(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.FileEntry
	err := Walk(dir, []string{".git"}, false, func(entry *models.FileEntry) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	for _, e := range entries {
		if filepath.Base(e.Path) == ".git" || filepath.Base(e.Path) == "config" {
			t.Errorf("should have excluded .git directory, found: %s", e.Path)
		}
	}
}

func TestWalk_WithHash(t *testing.T) {
	dir := setupTestTree(t)

	var hashed int
	err := Walk(dir, nil, true, func(entry *models.FileEntry) error {
		if !entry.IsDir && entry.ContentHash != "" {
			hashed++
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	if hashed == 0 {
		t.Error("expected at least one file to have a hash")
	}
}
