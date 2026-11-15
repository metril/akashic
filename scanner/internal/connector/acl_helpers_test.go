package connector

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// aclType returns the ACL union's discriminator ("" for nil).
func aclType(a *apiclient.ACLInput) string {
	if a == nil {
		return ""
	}
	d, _ := a.Discriminator()
	return d
}

// cloudGrants unwraps a cloud_drive ACL's grants.
func cloudGrants(t *testing.T, a *apiclient.ACLInput) []apiclient.CloudDriveGrantInput {
	t.Helper()
	v, err := a.AsCloudDriveACLInput()
	if err != nil {
		t.Fatalf("not a cloud_drive ACL: %v", err)
	}
	return wire.Deref(v.Grants)
}
