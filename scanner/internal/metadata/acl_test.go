package metadata

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCollectACL_PosixFallback(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "f.txt")
	if err := os.WriteFile(path, []byte("x"), 0644); err != nil {
		t.Fatal(err)
	}
	acl, err := CollectACL(path)
	if err != nil {
		t.Fatal(err)
	}
	// On a fresh tmpfile with only standard rwx, ACL may be nil — but if returned, must be POSIX-typed.
	if acl != nil && acl.Type != "posix" {
		t.Errorf("expected nil or posix ACL, got %s", acl.Type)
	}
}
