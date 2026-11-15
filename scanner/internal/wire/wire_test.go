package wire

import (
	"encoding/json"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

func marshal(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// The ACL constructors must emit the discriminated wire shape the API
// validates, with required lists present (never null).
func TestACLConstructors_WireShape(t *testing.T) {
	cases := []struct {
		name string
		acl  *apiclient.ACLInput
		want string
	}{
		{"posix nil entries", PosixACL(nil, nil), `{"entries":[],"type":"posix"}`},
		{"posix defaults", PosixACL(
			[]apiclient.PosixACEInput{{Tag: "user_obj", Perms: "rwx"}},
			[]apiclient.PosixACEInput{{Tag: "mask", Perms: "r-x"}},
		), `{"default_entries":[{"perms":"r-x","tag":"mask"}],"entries":[{"perms":"rwx","tag":"user_obj"}],"type":"posix"}`},
		{"nfsv4 nil entries", NfsV4ACL(nil), `{"entries":[],"type":"nfsv4"}`},
		{"nt nil entries", NtACL(apiclient.NtACLInput{}), `{"entries":[],"type":"nt"}`},
		{"s3 nil grants", S3ACL(apiclient.S3ACLInput{}), `{"grants":[],"type":"s3"}`},
		{"cloud_drive domain", CloudDriveACL(nil, "example.com"),
			`{"domain_restricted_to":"example.com","grants":[],"type":"cloud_drive"}`},
		{"cloud_drive no domain", CloudDriveACL(nil, ""), `{"grants":[],"type":"cloud_drive"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := marshal(t, tc.acl); got != tc.want {
				t.Errorf("got  %s\nwant %s", got, tc.want)
			}
		})
	}
}

func TestStrHelpers(t *testing.T) {
	if Str("") != nil || *Str("a") != "a" {
		t.Error("Str: empty must be nil, non-empty a pointer")
	}
	if Strs(nil) != nil || len(*Strs([]string{"a"})) != 1 {
		t.Error("Strs: empty must be nil, non-empty a pointer")
	}
	if Deref[int](nil) != 0 || Deref(To(3)) != 3 {
		t.Error("Deref")
	}
}

func TestEntryKind(t *testing.T) {
	d := &apiclient.EntryIn{Kind: KindOf(true)}
	f := &apiclient.EntryIn{Kind: KindOf(false)}
	if !IsDir(d) || IsDir(f) || IsDir(&apiclient.EntryIn{}) {
		t.Error("IsDir/KindOf mismatch")
	}
}
