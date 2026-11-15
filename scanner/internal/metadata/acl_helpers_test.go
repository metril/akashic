package metadata

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// aclType returns the ACL union's discriminator ("" for nil).
func aclType(a *apiclient.ACLInput) string {
	if a == nil {
		return ""
	}
	d, _ := a.Discriminator()
	return d
}

// posixACL unwraps a posix ACL.
func posixACL(t *testing.T, a *apiclient.ACLInput) apiclient.PosixACLInput {
	t.Helper()
	v, err := a.AsPosixACLInput()
	if err != nil {
		t.Fatalf("not a posix ACL: %v", err)
	}
	return v
}

// posixEntries unwraps a posix ACL's access entries.
func posixEntries(t *testing.T, a *apiclient.ACLInput) []apiclient.PosixACEInput {
	t.Helper()
	return posixACL(t, a).Entries
}

// nfsEntries unwraps an nfsv4 ACL's entries.
func nfsEntries(t *testing.T, a *apiclient.ACLInput) []apiclient.NfsV4ACEInput {
	t.Helper()
	v, err := a.AsNfsV4ACLInput()
	if err != nil {
		t.Fatalf("not an nfsv4 ACL: %v", err)
	}
	return v.Entries
}

// ntACL unwraps an nt ACL.
func ntACL(t *testing.T, a *apiclient.ACLInput) apiclient.NtACLInput {
	t.Helper()
	v, err := a.AsNtACLInput()
	if err != nil {
		t.Fatalf("not an nt ACL: %v", err)
	}
	return v
}
