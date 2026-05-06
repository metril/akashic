package connector

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/Azure/azure-sdk-for-go/sdk/azcore/to"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/container"
)

func TestNormalisePrefix(t *testing.T) {
	cases := map[string]*string{
		"":            nil,
		"/":           nil,
		"foo":         to.Ptr("foo"),
		"/foo":        to.Ptr("foo"),
		"/foo/":       to.Ptr("foo/"),
		"foo/bar/":    to.Ptr("foo/bar/"),
	}
	for in, want := range cases {
		got := normalisePrefix(in)
		switch {
		case got == nil && want == nil:
			continue
		case got == nil || want == nil:
			t.Errorf("normalisePrefix(%q): nil mismatch (want %v)", in, want)
		case *got != *want:
			t.Errorf("normalisePrefix(%q) = %q, want %q", in, *got, *want)
		}
	}
}

func TestPathSegmentsExcluded(t *testing.T) {
	set := map[string]bool{".tmp": true, ".trash": true}
	cases := []struct {
		name string
		want bool
	}{
		{"docs/file.pdf", false},
		{".tmp/foo", true},
		{"a/.trash/b", true},
		{"plain.txt", false},
		// Case-insensitive against the lowercase set.
		{"a/.TMP/b", true},
	}
	for _, c := range cases {
		if got := pathSegmentsExcluded(c.name, set); got != c.want {
			t.Errorf("pathSegmentsExcluded(%q) = %v, want %v", c.name, got, c.want)
		}
	}
	// Empty set short-circuits to false regardless of name.
	if pathSegmentsExcluded("anything", nil) {
		t.Error("empty exclude set should not exclude anything")
	}
}

func TestBuildAzureBlobEntry(t *testing.T) {
	mod := time.Date(2024, 9, 16, 14, 0, 0, 0, time.UTC)
	size := int64(12345)
	md5 := []byte{0xde, 0xad, 0xbe, 0xef}
	blob := &container.BlobItem{
		Name: to.Ptr("photos/2024/IMG_001.jpg"),
		Properties: &container.BlobProperties{
			ContentLength: &size,
			LastModified:  &mod,
			ContentMD5:    md5,
		},
	}
	entry := buildAzureBlobEntry(blob, *blob.Name)
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
	if entry.SizeBytes == nil || *entry.SizeBytes != size {
		t.Errorf("SizeBytes = %v, want %d", entry.SizeBytes, size)
	}
	if entry.ModifiedAt == nil || !entry.ModifiedAt.Equal(mod) {
		t.Errorf("ModifiedAt = %v, want %v", entry.ModifiedAt, mod)
	}
	if entry.ContentHash != "md5:deadbeef" {
		t.Errorf("ContentHash = %q, want md5:deadbeef", entry.ContentHash)
	}
}

func TestAzureBlobAuthModeValidation(t *testing.T) {
	// All Connect() validations run BEFORE any network IO, so a
	// background context is fine — the field-level errors return
	// synchronously.
	ctx := context.Background()

	c := NewAzureBlobConnector("acct", "container", "account_key", "", "", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "account_key required") {
		t.Errorf("missing account_key: want account_key required, got %v", err)
	}

	c = NewAzureBlobConnector("acct", "container", "sas_token", "", "", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "sas_token required") {
		t.Errorf("missing sas_token: want sas_token required, got %v", err)
	}

	c = NewAzureBlobConnector("acct", "container", "bogus_mode", "", "", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "unsupported auth_mode") {
		t.Errorf("bogus auth_mode: want unsupported auth_mode, got %v", err)
	}
}

func TestAzureBlobMissingRequiredFields(t *testing.T) {
	ctx := context.Background()
	c := NewAzureBlobConnector("", "container", "account_key", "k", "", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "account_name required") {
		t.Errorf("missing account_name: want account_name required, got %v", err)
	}
	c = NewAzureBlobConnector("acct", "", "account_key", "k", "", "")
	if err := c.Connect(ctx); err == nil || !strings.Contains(err.Error(), "container required") {
		t.Errorf("missing container: want container required, got %v", err)
	}
}
