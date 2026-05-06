package connector

import (
	"context"
	"strings"
	"testing"
	"time"

	"cloud.google.com/go/storage"
)

func TestJoinPrefix(t *testing.T) {
	cases := []struct {
		static, perCall, want string
	}{
		{"", "", ""},
		{"data", "", "data"},
		{"", "sub", "sub"},
		{"data", "sub", "data/sub"},
		{"/data/", "/sub/", "data/sub"},
		{"a/b", "c/d", "a/b/c/d"},
	}
	for _, c := range cases {
		if got := joinPrefix(c.static, c.perCall); got != c.want {
			t.Errorf("joinPrefix(%q, %q) = %q, want %q", c.static, c.perCall, got, c.want)
		}
	}
}

func TestBuildGCSEntry(t *testing.T) {
	mod := time.Date(2024, 9, 16, 14, 0, 0, 0, time.UTC)
	obj := &storage.ObjectAttrs{
		Name:        "photos/2024/IMG_001.jpg",
		Size:        12345,
		Updated:     mod,
		MD5:         []byte{0xde, 0xad, 0xbe, 0xef},
		ContentType: "image/jpeg",
	}
	entry := buildGCSEntry(obj, obj.Name)
	if entry.Path != "photos/2024/IMG_001.jpg" {
		t.Errorf("Path = %q, want photos/2024/IMG_001.jpg", entry.Path)
	}
	if entry.Name != "IMG_001.jpg" {
		t.Errorf("Name = %q, want IMG_001.jpg", entry.Name)
	}
	if entry.Kind != "file" {
		t.Errorf("Kind = %q, want file", entry.Kind)
	}
	if entry.Extension != "jpg" {
		t.Errorf("Extension = %q, want jpg", entry.Extension)
	}
	if entry.MimeType != "image/jpeg" {
		t.Errorf("MimeType = %q, want image/jpeg", entry.MimeType)
	}
	if entry.SizeBytes == nil || *entry.SizeBytes != 12345 {
		t.Errorf("SizeBytes = %v, want 12345", entry.SizeBytes)
	}
	if !entry.ModifiedAt.Equal(mod) {
		t.Errorf("ModifiedAt = %v, want %v", entry.ModifiedAt, mod)
	}
	if entry.ContentHash != "md5:deadbeef" {
		t.Errorf("ContentHash = %q, want md5:deadbeef", entry.ContentHash)
	}
}

func TestBuildGCSEntryCRC32CFallback(t *testing.T) {
	obj := &storage.ObjectAttrs{
		Name:       "data.bin",
		Size:       100,
		CRC32C:     0x12345678,
		Generation: 17,
	}
	entry := buildGCSEntry(obj, obj.Name)
	if !strings.HasPrefix(entry.ContentHash, "crc32c:") {
		t.Errorf("expected crc32c:-prefixed hash for MD5-less object, got %q", entry.ContentHash)
	}
	// Generation + size are baked in so a re-upload (different
	// generation) doesn't dedup against the prior version.
	if !strings.Contains(entry.ContentHash, "gen:17") || !strings.Contains(entry.ContentHash, "size:100") {
		t.Errorf("crc32c hash missing generation or size: %q", entry.ContentHash)
	}
}

func TestGCSAuthModeValidation(t *testing.T) {
	ctx := context.Background()

	// service_account_json without the JSON content fails fast
	// before any network IO.
	c := NewGCSConnector("my-bucket", "", "service_account_json", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "service_account_json required") {
		t.Errorf("missing JSON: want service_account_json required, got %v", err)
	}

	c = NewGCSConnector("my-bucket", "", "bogus_mode", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "unsupported auth_mode") {
		t.Errorf("bogus auth_mode: want unsupported auth_mode, got %v", err)
	}
}

func TestGCSMissingBucket(t *testing.T) {
	c := NewGCSConnector("", "", "service_account_json", "{}")
	if err := c.Connect(context.Background()); err == nil || !strings.Contains(err.Error(), "bucket required") {
		t.Errorf("missing bucket: want bucket required, got %v", err)
	}
}

func TestGCSInvalidServiceAccountJSON(t *testing.T) {
	// Garbage JSON gets caught by storage.NewClient before any
	// network IO. The exact wrapping varies by SDK version, so we
	// only assert that some error was returned without coupling to
	// the SDK's message.
	c := NewGCSConnector("my-bucket", "", "service_account_json", "{not-json")
	err := c.Connect(context.Background())
	if err == nil {
		t.Fatalf("expected error for invalid service account JSON")
	}
	if !strings.Contains(err.Error(), "gcs:") {
		t.Errorf("error not prefixed with 'gcs:': %v", err)
	}
}
