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

	owners := NewOwnerResolver()
	entry, err := Collect(path, true, owners)
	if err != nil {
		t.Fatal(err)
	}

	if entry.Name != "test.txt" {
		t.Errorf("expected name test.txt, got %s", entry.Name)
	}
	if entry.Kind != "file" {
		t.Errorf("expected kind file, got %s", entry.Kind)
	}
	if entry.Extension != "txt" {
		t.Errorf("expected extension txt, got %s", entry.Extension)
	}
	if entry.SizeBytes == nil || *entry.SizeBytes != 11 {
		t.Errorf("expected size 11, got %v", entry.SizeBytes)
	}
	if entry.ContentHash == "" {
		t.Error("expected non-empty content hash")
	}
	if entry.Mode == nil {
		t.Error("expected mode to be captured")
	}
	if entry.Uid == nil {
		t.Error("expected uid to be captured")
	}
}

func TestCollect_Directory(t *testing.T) {
	dir := t.TempDir()
	subdir := filepath.Join(dir, "subdir")
	if err := os.Mkdir(subdir, 0755); err != nil {
		t.Fatal(err)
	}

	entry, err := Collect(subdir, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	if entry.Kind != "directory" {
		t.Errorf("expected kind directory, got %s", entry.Kind)
	}
	if entry.ContentHash != "" {
		t.Error("expected empty content hash for directory")
	}
	if entry.SizeBytes != nil {
		t.Error("expected nil size for directory")
	}
}

func TestCollect_WithHash(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "hashme.bin")
	if err := os.WriteFile(path, []byte("deterministic content"), 0644); err != nil {
		t.Fatal(err)
	}

	entry1, _ := Collect(path, true, nil)
	entry2, _ := Collect(path, true, nil)

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

	entry, err := Collect(path, false, nil)
	if err != nil {
		t.Fatal(err)
	}

	if entry.ContentHash != "" {
		t.Error("expected empty hash when computeHash=false")
	}
}

func TestCollect_OwnerResolution(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "owned.txt")
	if err := os.WriteFile(path, []byte("hi"), 0644); err != nil {
		t.Fatal(err)
	}

	owners := NewOwnerResolver()
	entry, err := Collect(path, false, owners)
	if err != nil {
		t.Fatal(err)
	}

	// At minimum, uid must be captured. Name resolution may fail in CI, that's
	// ok — it should be empty string, not crash.
	if entry.Uid == nil {
		t.Error("expected uid to be captured")
	}
}
