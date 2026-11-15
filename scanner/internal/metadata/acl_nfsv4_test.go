package metadata

import (
	"reflect"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

func TestParseNfsV4ACL_AllowAndDeny(t *testing.T) {
	raw := `A::OWNER@:rwatTnNcCy
A:fd:GROUP@:rxtncy
D::EVERYONE@:wadDxoy
A::alice@example.com:rwatTnNcy
`
	got := parseNfsV4ACL(raw)
	want := []apiclient.NfsV4ACEInput{
		{
			Principal: "OWNER@",
			AceType:   apiclient.NfsV4ACEInputAceTypeAllow,
			Mask: wire.To([]string{
				"read_data", "write_data", "append_data", "read_attributes",
				"write_attributes", "read_named_attrs", "write_named_attrs",
				"read_acl", "write_acl", "synchronize",
			}),
		},
		{
			Principal: "GROUP@",
			AceType:   apiclient.NfsV4ACEInputAceTypeAllow,
			Flags:     wire.To([]string{"file_inherit", "dir_inherit"}),
			Mask:      wire.To([]string{"read_data", "execute", "read_attributes", "read_named_attrs", "read_acl", "synchronize"}),
		},
		{
			Principal: "EVERYONE@",
			AceType:   apiclient.NfsV4ACEInputAceTypeDeny,
			Mask:      wire.To([]string{"write_data", "append_data", "delete", "delete_child", "execute", "write_owner", "synchronize"}),
		},
		{
			Principal: "alice@example.com",
			AceType:   apiclient.NfsV4ACEInputAceTypeAllow,
			Mask:      wire.To([]string{"read_data", "write_data", "append_data", "read_attributes", "write_attributes", "read_named_attrs", "write_named_attrs", "read_acl", "synchronize"}),
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
