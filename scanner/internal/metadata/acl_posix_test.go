package metadata

import (
	"reflect"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

func TestParsePosixACL_AccessOnly(t *testing.T) {
	raw := "user::rwx\nuser:alice:r-x\ngroup::r-x\nmask::r-x\nother::r-x\n"
	access, def := parsePosixACL(raw)
	if def != nil {
		t.Errorf("expected nil default entries, got %v", def)
	}
	want := []apiclient.PosixACEInput{
		{Tag: "user_obj", Qualifier: nil, Perms: "rwx"},
		{Tag: "user", Qualifier: wire.To("alice"), Perms: "r-x"},
		{Tag: "group_obj", Qualifier: nil, Perms: "r-x"},
		{Tag: "mask", Qualifier: nil, Perms: "r-x"},
		{Tag: "other", Qualifier: nil, Perms: "r-x"},
	}
	if !reflect.DeepEqual(access, want) {
		t.Errorf("got %v, want %v", access, want)
	}
}

func TestParsePosixACL_WithDefaults(t *testing.T) {
	raw := `user::rwx
group::r-x
other::r-x
default:user::rwx
default:user:alice:r--
default:group::r-x
default:mask::r-x
default:other::r--
`
	access, def := parsePosixACL(raw)
	if len(access) != 3 {
		t.Errorf("expected 3 access entries, got %d", len(access))
	}
	if len(def) != 5 {
		t.Errorf("expected 5 default entries, got %d", len(def))
	}
	if def[1].Tag != "user" || wire.Deref(def[1].Qualifier) != "alice" || def[1].Perms != "r--" {
		t.Errorf("default user:alice mismatch: %+v", def[1])
	}
}

func TestParsePosixACL_SkipsCommentsAndBlank(t *testing.T) {
	raw := "# file: /tmp/x\n\n# owner: root\nuser::rwx\n"
	access, def := parsePosixACL(raw)
	if len(access) != 1 || def != nil {
		t.Errorf("got access=%v default=%v", access, def)
	}
}
