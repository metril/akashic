package sddl

import "testing"

func TestParseSecurityDescriptor_OwnerGroupAndDacl(t *testing.T) {
	owner := BuildSID(5, 21, 100, 200, 300, 1013)
	group := BuildSID(5, 21, 100, 200, 300, 513)
	aliceSID := BuildSID(5, 21, 100, 200, 300, 1013)
	everyoneSID := BuildSID(1, 0)

	dacl := BuildACL(
		BuildACE(0x00, 0x00, 0x001F01FF, aliceSID),
		BuildACE(0x01, 0x00, 0x00000020, everyoneSID),
	)

	sd := BuildSecurityDescriptor(0x8004, owner, group, dacl)

	parsed, err := ParseSecurityDescriptor(sd)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.OwnerSID != "S-1-5-21-100-200-300-1013" {
		t.Errorf("owner: got %q", parsed.OwnerSID)
	}
	if parsed.GroupSID != "S-1-5-21-100-200-300-513" {
		t.Errorf("group: got %q", parsed.GroupSID)
	}
	if len(parsed.DaclEntries) != 2 {
		t.Fatalf("expected 2 dacl entries, got %d", len(parsed.DaclEntries))
	}
	a0 := parsed.DaclEntries[0]
	if a0.SID != "S-1-5-21-100-200-300-1013" || a0.AceType != "allow" {
		t.Errorf("ace0 wrong: %+v", a0)
	}
	a1 := parsed.DaclEntries[1]
	if a1.SID != "S-1-1-0" || a1.AceType != "deny" {
		t.Errorf("ace1 wrong: %+v", a1)
	}
	contains := func(s []string, want string) bool {
		for _, x := range s {
			if x == want {
				return true
			}
		}
		return false
	}
	if !contains(a1.Mask, "EXECUTE") {
		t.Errorf("ace1 missing EXECUTE in mask: %v", a1.Mask)
	}
}

func TestParseSecurityDescriptor_NullOwner(t *testing.T) {
	sd := BuildSecurityDescriptor(0x8000, nil, nil, nil)
	parsed, err := ParseSecurityDescriptor(sd)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.OwnerSID != "" || parsed.GroupSID != "" {
		t.Errorf("expected empty owner/group, got %q %q", parsed.OwnerSID, parsed.GroupSID)
	}
	if len(parsed.DaclEntries) != 0 {
		t.Errorf("expected no DACL entries")
	}
}
