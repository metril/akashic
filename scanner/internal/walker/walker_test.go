package walker

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"sync/atomic"
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
	_, err := Walk(context.Background(), dir, nil, false, func(entry *models.EntryRecord) error {
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
	_, err := Walk(context.Background(), dir, []string{".git"}, false, func(entry *models.EntryRecord) error {
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
	_, err := Walk(context.Background(), dir, nil, true, func(entry *models.EntryRecord) error {
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
	_, werr := Walk(context.Background(), dir, nil, false, func(e *models.EntryRecord) error {
		if e.IsDir() {
			dirs[filepath.Base(e.Path)] = e
		}
		return nil
	})
	must(werr)

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

// Walk respects context cancellation: a SIGTERM / scan-cancel
// triggered partway through a multi-million-file walk must return
// promptly with ctx.Err(), not run to completion. Pre-fix the
// Walker discarded ctx entirely.
func TestWalk_HonoursContextCancellation(t *testing.T) {
	dir := t.TempDir()
	// Build a wide tree so the cancellation has somewhere to land.
	for i := 0; i < 10; i++ {
		sub := filepath.Join(dir, "sub")
		os.MkdirAll(sub, 0o755)
		os.WriteFile(filepath.Join(sub, "f"), []byte("x"), 0o644)
	}
	for i := 0; i < 100; i++ {
		os.WriteFile(filepath.Join(dir, "f"+string(rune('0'+i%10))+string(rune('0'+i/10))), []byte("y"), 0o644)
	}

	ctx, cancel := context.WithCancel(context.Background())
	var seen atomic.Int64
	_, err := Walk(ctx, dir, nil, false, func(e *models.EntryRecord) error {
		// Cancel as soon as the first entry comes through. The next
		// directory boundary should bail out.
		if seen.Add(1) == 1 {
			cancel()
		}
		return nil
	})

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
}

// v0.5.11 — WalkStats counts entries the walker silently skipped
// (permission denied / mid-scan ENOENT). Pre-fix, these were swallowed
// with no record; the api had no way to surface "this scan touched
// dirs it couldn't enter."
func TestWalk_AccountsInaccessibleDirs(t *testing.T) {
	if os.Getuid() == 0 {
		t.Skip("running as root — chmod 000 doesn't deny root reads")
	}
	dir := t.TempDir()
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	must(os.MkdirAll(filepath.Join(dir, "open"), 0o755))
	must(os.WriteFile(filepath.Join(dir, "open", "x.txt"), []byte("ok"), 0o644))
	must(os.MkdirAll(filepath.Join(dir, "denied"), 0o755))
	must(os.WriteFile(filepath.Join(dir, "denied", "secret.txt"), []byte("nope"), 0o644))
	// Lock the second subdir so ReadDir fails on it.
	must(os.Chmod(filepath.Join(dir, "denied"), 0o000))
	t.Cleanup(func() { _ = os.Chmod(filepath.Join(dir, "denied"), 0o755) })

	stats, err := Walk(context.Background(), dir, nil, false, func(*models.EntryRecord) error {
		return nil
	})
	if err != nil {
		t.Fatalf("walk: %v", err)
	}
	if stats.InaccessibleDirs < 1 {
		t.Errorf("expected at least 1 inaccessible dir, got %d", stats.InaccessibleDirs)
	}
}

// TestWalkShallow verifies the shallow-walk mode used by the
// unit-coordinated agent: emit files at the root level, return
// subdirectory names instead of recursing into them.
func TestWalkShallow_FilesEmittedSubdirsReturned(t *testing.T) {
	dir := setupTestTree(t)

	var entries []*models.EntryRecord
	res, err := WalkShallow(context.Background(), dir, nil, false, func(entry *models.EntryRecord) error {
		entries = append(entries, entry)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	// Files at the root: just file1.txt
	gotFiles := map[string]bool{}
	for _, e := range entries {
		gotFiles[filepath.Base(e.Path)] = true
	}
	if !gotFiles["file1.txt"] {
		t.Errorf("expected file1.txt in shallow walk, got %v", gotFiles)
	}
	// Subdirectories returned, not recursed into:
	if len(res.SubdirNames) != 1 || res.SubdirNames[0] != "subdir" {
		t.Errorf("expected SubdirNames=[subdir], got %v", res.SubdirNames)
	}
	// No nested file emitted (recursion did NOT happen):
	if gotFiles["file2.log"] {
		t.Errorf("WalkShallow recursed into subdir; file2.log should not be emitted")
	}
}

func TestWalkShallow_HonoursExcludePatterns(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "keep.txt"), []byte("k"), 0o644)
	os.WriteFile(filepath.Join(dir, "skip.txt"), []byte("s"), 0o644)
	os.MkdirAll(filepath.Join(dir, "node_modules"), 0o755)
	os.MkdirAll(filepath.Join(dir, "src"), 0o755)

	var emitted []string
	res, err := WalkShallow(
		context.Background(), dir,
		[]string{"skip.txt", "node_modules"}, false,
		func(e *models.EntryRecord) error {
			emitted = append(emitted, filepath.Base(e.Path))
			return nil
		},
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(emitted) != 1 || emitted[0] != "keep.txt" {
		t.Errorf("expected emitted=[keep.txt], got %v", emitted)
	}
	if len(res.SubdirNames) != 1 || res.SubdirNames[0] != "src" {
		t.Errorf("expected SubdirNames=[src] (node_modules excluded), got %v", res.SubdirNames)
	}
}

func TestWalkShallow_UnreadableRootReturnsZero(t *testing.T) {
	res, err := WalkShallow(
		context.Background(), "/no/such/path/akashic",
		nil, false,
		func(*models.EntryRecord) error { return nil },
	)
	if err != nil {
		t.Fatalf("expected nil err on unreadable root (parity with Walk), got %v", err)
	}
	if len(res.SubdirNames) != 0 {
		t.Errorf("expected zero SubdirNames on unreadable root, got %v", res.SubdirNames)
	}
}
