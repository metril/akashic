package metadata

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/metadata/sddl"
)

func TestSDToNtACL_OwnerResolvedToWellKnown(t *testing.T) {
	owner := sddl.BuildSID(5, 18) // S-1-5-18 = NT AUTHORITY\SYSTEM
	sd := sddl.BuildSecurityDescriptor(0x8004, owner, nil, nil)
	acl, err := SDToNtACL(sd, nil)
	if err != nil {
		t.Fatal(err)
	}
	if acl.Type != "nt" {
		t.Errorf("type=%q", acl.Type)
	}
	if acl.Owner == nil || acl.Owner.Name != "NT AUTHORITY\\SYSTEM" {
		t.Errorf("expected SYSTEM owner with friendly name, got %+v", acl.Owner)
	}
}

func TestSDToNtACL_DomainSIDLeftRaw(t *testing.T) {
	owner := sddl.BuildSID(5, 21, 100, 200, 300, 1013)
	sd := sddl.BuildSecurityDescriptor(0x8004, owner, nil, nil)
	acl, err := SDToNtACL(sd, nil)
	if err != nil {
		t.Fatal(err)
	}
	if acl.Owner.Name != "" {
		t.Errorf("expected empty name for domain SID, got %q", acl.Owner.Name)
	}
	if acl.Owner.Sid != "S-1-5-21-100-200-300-1013" {
		t.Errorf("got sid %q", acl.Owner.Sid)
	}
}
