package metadata

import (
	"reflect"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
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
	if got["/etc/passwd"] == nil || aclType(got["/etc/passwd"]) != "posix" {
		t.Errorf("missing or wrong-typed entry for /etc/passwd: %+v", got["/etc/passwd"])
	}
	foo := got["/tmp/foo"]
	if foo == nil {
		t.Fatal("missing /tmp/foo")
	}
	wantBob := apiclient.PosixACEInput{Tag: "user", Qualifier: wire.To("bob"), Perms: "r-x"}
	found := false
	fooEntries := posixEntries(t, foo)
	for _, e := range fooEntries {
		if reflect.DeepEqual(e, wantBob) {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("expected user:bob:r-x in /tmp/foo entries, got %+v", fooEntries)
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
	if len(wire.Deref(posixACL(t, dir).DefaultEntries)) != 4 {
		t.Errorf("expected 4 default entries, got %d", len(wire.Deref(posixACL(t, dir).DefaultEntries)))
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
	if aclType(got["/tmp/foo"]) != "nfsv4" || len(nfsEntries(t, got["/tmp/foo"])) != 3 {
		t.Errorf("unexpected /tmp/foo: %+v", got["/tmp/foo"])
	}
}
