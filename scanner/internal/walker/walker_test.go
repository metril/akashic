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

func TestWalk_AllEntries(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.EntryRecord
	err := Walk(dir, nil, false, func(entry *models.EntryRecord) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	if len(entries) < 5 {
		t.Errorf("expected at least 5 entries (files + dirs), got %d", len(entries))
	}

	files, dirs := 0, 0
	for _, e := range entries {
		if e.IsDir() {
			dirs++
		} else {
			files++
		}
	}
	if files == 0 {
		t.Error("expected at least one file entry")
	}
	if dirs == 0 {
		t.Error("expected at least one directory entry")
	}
}

func TestWalk_ExcludePatterns(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.EntryRecord
	err := Walk(dir, []string{".git"}, false, func(entry *models.EntryRecord) error {
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
	err := Walk(dir, nil, true, func(entry *models.EntryRecord) error {
		if !entry.IsDir() && entry.ContentHash != "" {
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

// Phase B — directory records emit post-order with subtree totals.
// Tree:
//
//	root/
//	├── a/
//	│   ├── x.txt   (5 bytes)
//	│   └── y.txt   (5 bytes)
//	└── b/
//	    └── c/
//	        └── z.bin (8 bytes)
//
// Expected:
//
//	a:    bytes=10, files=2, dirs=0
//	c:    bytes=8,  files=1, dirs=0
//	b:    bytes=8,  files=1, dirs=1
func TestWalk_PostOrderSubtreeTotals(t *testing.T) {
	dir := t.TempDir()
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(os.MkdirAll(filepath.Join(dir, "a"), 0o755))
	must(os.MkdirAll(filepath.Join(dir, "b", "c"), 0o755))
	must(os.WriteFile(filepath.Join(dir, "a", "x.txt"), []byte("hello"), 0o644))
	must(os.WriteFile(filepath.Join(dir, "a", "y.txt"), []byte("world"), 0o644))
	must(os.WriteFile(filepath.Join(dir, "b", "c", "z.bin"), []byte("12345678"), 0o644))

	dirs := map[string]*models.EntryRecord{}
	must(Walk(dir, nil, false, func(e *models.EntryRecord) error {
		if e.IsDir() {
			dirs[filepath.Base(e.Path)] = e
		}
		return nil
	}))

	for _, name := range []string{"a", "b", "c"} {
		if _, ok := dirs[name]; !ok {
			t.Fatalf("expected directory %q in walk output", name)
		}
		if dirs[name].SubtreeSizeBytes == nil {
			t.Errorf("dir %q: SubtreeSizeBytes is nil; expected post-order rollup to fill it", name)
		}
	}

	if got := *dirs["a"].SubtreeSizeBytes; got != 10 {
		t.Errorf("a: SubtreeSizeBytes=%d, want 10", got)
	}
	if got := *dirs["a"].SubtreeFileCount; got != 2 {
		t.Errorf("a: SubtreeFileCount=%d, want 2", got)
	}
	if got := *dirs["a"].SubtreeDirCount; got != 0 {
		t.Errorf("a: SubtreeDirCount=%d, want 0", got)
	}

	if got := *dirs["c"].SubtreeSizeBytes; got != 8 {
		t.Errorf("c: SubtreeSizeBytes=%d, want 8", got)
	}
	if got := *dirs["c"].SubtreeFileCount; got != 1 {
		t.Errorf("c: SubtreeFileCount=%d, want 1", got)
	}

	// b inherits c's totals + 1 dir for c itself.
	if got := *dirs["b"].SubtreeSizeBytes; got != 8 {
		t.Errorf("b: SubtreeSizeBytes=%d, want 8", got)
	}
	if got := *dirs["b"].SubtreeFileCount; got != 1 {
		t.Errorf("b: SubtreeFileCount=%d, want 1", got)
	}
	if got := *dirs["b"].SubtreeDirCount; got != 1 {
		t.Errorf("b: SubtreeDirCount=%d, want 1", got)
	}
}
