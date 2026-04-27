package metadata

import (
	"reflect"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestParseNfsV4ACL_AllowAndDeny(t *testing.T) {
	raw := `A::OWNER@:rwatTnNcCy
A:fd:GROUP@:rxtncy
D::EVERYONE@:wadDxoy
A::alice@example.com:rwatTnNcy
`
	got := parseNfsV4ACL(raw)
	want := []models.NfsV4ACE{
		{
			Principal: "OWNER@",
			AceType:   "allow",
			Flags:     []string{},
			Mask: []string{
				"read_data", "write_data", "append_data", "read_attributes",
				"write_attributes", "read_named_attrs", "write_named_attrs",
				"read_acl", "write_acl", "synchronize",
			},
		},
		{
			Principal: "GROUP@",
			AceType:   "allow",
			Flags:     []string{"file_inherit", "dir_inherit"},
			Mask:      []string{"read_data", "execute", "read_attributes", "read_named_attrs", "read_acl", "synchronize"},
		},
		{
			Principal: "EVERYONE@",
			AceType:   "deny",
			Flags:     []string{},
			Mask:      []string{"write_data", "append_data", "delete", "delete_child", "execute", "write_owner", "synchronize"},
		},
		{
			Principal: "alice@example.com",
			AceType:   "allow",
			Flags:     []string{},
			Mask:      []string{"read_data", "write_data", "append_data", "read_attributes", "write_attributes", "read_named_attrs", "write_named_attrs", "read_acl", "synchronize"},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("\ngot  %#v\nwant %#v", got, want)
	}
}

func TestParseNfsV4ACL_SkipsBlanks(t *testing.T) {
	raw := "\n# comment\nA::OWNER@:r\n"
	got := parseNfsV4ACL(raw)
	if len(got) != 1 {
		t.Errorf("expected 1 entry, got %d: %v", len(got), got)
	}
}
