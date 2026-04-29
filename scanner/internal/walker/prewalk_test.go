package walker

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPrewalkCountsFilesDirsBytes(t *testing.T) {
	root := t.TempDir()

	// 3 files (1, 2, 3 bytes) + 2 dirs (sub, sub/inner). One file lives
	// inside inner so the walker has to descend.
	mustWrite(t, filepath.Join(root, "a"), "1")
	mustWrite(t, filepath.Join(root, "b"), "22")
	if err := os.MkdirAll(filepath.Join(root, "sub", "inner"), 0o755); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(root, "sub", "inner", "c"), "333")

	res, err := Prewalk(root, nil, nil, 0)
	if err != nil {
		t.Fatalf("prewalk: %v", err)
	}
	if res.Files != 3 {
		t.Errorf("files: got %d, want 3", res.Files)
	}
	if res.Dirs != 2 {
		t.Errorf("dirs: got %d, want 2", res.Dirs)
	}
	if res.Bytes != 6 {
		t.Errorf("bytes: got %d, want 6", res.Bytes)
	}
}

func TestPrewalkRespectsExcludes(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "node_modules", "deep"), 0o755); err != nil {
		t.Fatal(err)
	}
	mustWrite(t, filepath.Join(root, "node_modules", "deep", "garbage"), "0123456789")
	mustWrite(t, filepath.Join(root, "wanted.txt"), "ok")

	res, err := Prewalk(root, []string{"node_modules"}, nil, 0)
	if err != nil {
		t.Fatalf("prewalk: %v", err)
	}
	if res.Files != 1 {
		t.Errorf("files: got %d, want 1 (node_modules excluded)", res.Files)
	}
	if res.Dirs != 0 {
		t.Errorf("dirs: got %d, want 0", res.Dirs)
	}
}

func TestPrewalkProgressFiresAtCadence(t *testing.T) {
	root := t.TempDir()
	for i := 0; i < 12; i++ {
		mustWrite(t, filepath.Join(root, byteName(i)), "x")
	}

	var ticks int
	_, err := Prewalk(root, nil, func(_, _, _ int64, _ string) {
		ticks++
	}, 5)
	if err != nil {
		t.Fatalf("prewalk: %v", err)
	}
	// 12 entries / 5 = 2 mid-walk ticks + 1 final tick = 3 calls.
	// Allow >=3 to insulate against unrelated entries (nothing else
	// should be in the temp dir, but be permissive).
	if ticks < 3 {
		t.Errorf("progress ticks: got %d, want >=3", ticks)
	}
}

func mustWrite(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func byteName(i int) string { return string(rune('a' + i)) }
