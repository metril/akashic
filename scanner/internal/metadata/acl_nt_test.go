package metadata

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/metadata/sddl"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

func TestSDToNtACL_OwnerResolvedToWellKnown(t *testing.T) {
	owner := sddl.BuildSID(5, 18) // S-1-5-18 = NT AUTHORITY\SYSTEM
	sd := sddl.BuildSecurityDescriptor(0x8004, owner, nil, nil)
	wrapped, err := SDToNtACL(sd, nil)
	if err != nil {
		t.Fatal(err)
	}
	if aclType(wrapped) != "nt" {
		t.Errorf("type=%q", aclType(wrapped))
	}
	acl := ntACL(t, wrapped)
	if acl.Owner == nil || wire.Deref(acl.Owner.Name) != "NT AUTHORITY\\SYSTEM" {
		t.Errorf("expected SYSTEM owner with friendly name, got %+v", acl.Owner)
	}
}

func TestSDToNtACL_DomainSIDLeftRaw(t *testing.T) {
	owner := sddl.BuildSID(5, 21, 100, 200, 300, 1013)
	sd := sddl.BuildSecurityDescriptor(0x8004, owner, nil, nil)
	wrapped, err := SDToNtACL(sd, nil)
	if err != nil {
		t.Fatal(err)
	}
	acl := ntACL(t, wrapped)
	if acl.Owner.Name != nil {
		t.Errorf("expected no name for domain SID, got %q", *acl.Owner.Name)
	}
	if acl.Owner.Sid != "S-1-5-21-100-200-300-1013" {
		t.Errorf("got sid %q", acl.Owner.Sid)
	}
}
