package metadata

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCollect_RegularFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "test.txt")
	if err := os.WriteFile(path, []byte("hello world"), 0644); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(path, true)
	if err != nil {
		t.Fatal(err)
	}

	if entry.Filename != "test.txt" {
		t.Errorf("expected filename test.txt, got %s", entry.Filename)
	}
	if entry.Extension != "txt" {
		t.Errorf("expected extension txt, got %s", entry.Extension)
	}
	if entry.SizeBytes != 11 {
		t.Errorf("expected size 11, got %d", entry.SizeBytes)
	}
	if entry.ContentHash == "" {
		t.Error("expected non-empty content hash")
	}
	if entry.IsDir {
		t.Error("expected IsDir to be false")
	}
}

func TestCollect_Directory(t *testing.T) {
	dir := t.TempDir()
	subdir := filepath.Join(dir, "subdir")
	if err := os.Mkdir(subdir, 0755); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(subdir, false)
	if err != nil {
		t.Fatal(err)
	}

	if !entry.IsDir {
		t.Error("expected IsDir to be true")
	}
	if entry.ContentHash != "" {
		t.Error("expected empty content hash for directory")
	}
}

func TestCollect_WithHash(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "hashme.bin")
	if err := os.WriteFile(path, []byte("deterministic content"), 0644); err != nil {
		t.Fatal(err)
	}

	entry1, _ := Collect(path, true)
	entry2, _ := Collect(path, true)

	if entry1.ContentHash != entry2.ContentHash {
		t.Error("same content should produce same hash")
	}
}

func TestCollect_SkipHash(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "nohash.txt")
	if err := os.WriteFile(path, []byte("no hash please"), 0644); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(path, false)
	if err != nil {
		t.Fatal(err)
	}

	if entry.ContentHash != "" {
		t.Error("expected empty hash when computeHash=false")
	}
}
