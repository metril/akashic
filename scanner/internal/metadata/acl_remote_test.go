package metadata

import (
	"reflect"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestParseRemotePosixDump_MultipleFiles(t *testing.T) {
	raw := `# file: /etc/passwd
# owner: root
# group: root
user::rw-
group::r--
other::r--

# file: /tmp/foo
# owner: alice
# group: alice
user::rwx
user:bob:r-x
group::r-x
mask::r-x
other::r--

`
	got := ParseRemotePosixDump(raw)
	if len(got) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(got))
	}
	if got["/etc/passwd"] == nil || got["/etc/passwd"].Type != "posix" {
		t.Errorf("missing or wrong-typed entry for /etc/passwd: %+v", got["/etc/passwd"])
	}
	foo := got["/tmp/foo"]
	if foo == nil {
		t.Fatal("missing /tmp/foo")
	}
	wantBob := models.PosixACE{Tag: "user", Qualifier: "bob", Perms: "r-x"}
	found := false
	for _, e := range foo.Entries {
		if reflect.DeepEqual(e, wantBob) {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected user:bob:r-x in /tmp/foo entries, got %+v", foo.Entries)
	}
}

func TestParseRemotePosixDump_HandlesDefaults(t *testing.T) {
	raw := `# file: /tmp/dir
# owner: alice
# group: alice
user::rwx
group::r-x
other::r-x
default:user::rwx
default:user:bob:r--
default:mask::r-x
default:other::r--

`
	got := ParseRemotePosixDump(raw)
	dir := got["/tmp/dir"]
	if dir == nil {
		t.Fatal("missing /tmp/dir")
	}
	if len(dir.DefaultEntries) != 4 {
		t.Errorf("expected 4 default entries, got %d", len(dir.DefaultEntries))
	}
}

func TestParseRemoteNfs4Dump_PerFileBlocks(t *testing.T) {
	raw := `# file: /tmp/foo
A::OWNER@:rwatTnNcCy
A::GROUP@:rxtncy
D::EVERYONE@:wadDxoy
# file: /tmp/bar
A::OWNER@:rwatTnNcCy
`
	got := ParseRemoteNfs4Dump(raw)
	if len(got) != 2 {
		t.Fatalf("expected 2 entries, got %d", len(got))
	}
	if got["/tmp/foo"].Type != "nfsv4" || len(got["/tmp/foo"].NfsV4Entries) != 3 {
		t.Errorf("unexpected /tmp/foo: %+v", got["/tmp/foo"])
	}
}
