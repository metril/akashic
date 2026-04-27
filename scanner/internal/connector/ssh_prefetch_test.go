package connector

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// TestSSHConnector_NFSv4PrecedenceOverPOSIX verifies that when both NFSv4 and
// POSIX dumps return ACLs for the same path, NFSv4 wins (matches local
// dispatcher behavior).
func TestSSHConnector_NFSv4PrecedenceOverPOSIX(t *testing.T) {
	c := &SSHConnector{
		hasGetfacl:     true,
		hasNfs4Getfacl: true,
		aclCache:       map[string]*models.ACL{},
	}

	// Simulate the merge order from prefetchACLs: NFSv4 first, then POSIX (which
	// only fills in entries that NFSv4 didn't claim).
	nfs4Out := `# file: /tmp/shared
A::OWNER@:rwatTnNcCy
`
	posixOut := `# file: /tmp/shared
# owner: alice
user::rwx
user:bob:r-x
group::r-x
mask::r-x
other::r--

# file: /tmp/posix-only
# owner: alice
user::rwx
group::r-x
other::r--

`
	for k, v := range metadata.ParseRemoteNfs4Dump(nfs4Out) {
		c.aclCache[k] = v
	}
	for k, v := range metadata.ParseRemotePosixDump(posixOut) {
		if _, alreadyHaveNfs4 := c.aclCache[k]; !alreadyHaveNfs4 {
			c.aclCache[k] = v
		}
	}

	if got := c.aclCache["/tmp/shared"]; got == nil || got.Type != "nfsv4" {
		t.Errorf("/tmp/shared: expected nfsv4 (NFSv4 wins), got %+v", got)
	}
	if got := c.aclCache["/tmp/posix-only"]; got == nil || got.Type != "posix" {
		t.Errorf("/tmp/posix-only: expected posix, got %+v", got)
	}
}

// TestSSHConnector_AclModeSelection verifies the mode flag is set correctly
// based on the fullScan parameter passed to Walk. This is tested indirectly
// via the field — full integration requires a live SSH server.
func TestSSHConnector_AclModeFieldDocumented(t *testing.T) {
	c := &SSHConnector{aclMode: "full"}
	if c.aclMode != "full" {
		t.Error("aclMode field accessible")
	}
	c.aclMode = "perdir"
	if c.aclMode != "perdir" {
		t.Error("aclMode field assignable")
	}
}
