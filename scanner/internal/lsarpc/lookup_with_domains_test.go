package lsarpc

import "testing"

// realLsaResponseDomain is a verbatim 280-byte LsarLookupSids2 response
// captured against an actual Samba domain controller (menoetius/OLYMPOS)
// for three input domain SIDs (RIDs 512, 513, 27106). It is the authoritative
// fixture for the parser — any change to NDR walking is regressed against
// the names this server returned: "Domain Admins", "Domain Users", and
// "Share Admin", all in the OLYMPOS domain.
//
// Each pair was hand-decoded from the Wireshark-style hex dump so the test
// can run without network. The previous parser produced empty names for
// every entry because of NDR field misalignment; this fixture pins the
// fix so we don't regress.
var realLsaResponseDomain = []byte{
	// LSAPR_REFERENCED_DOMAIN_LIST (top-level)
	0x10, 0x00, 0x02, 0x00, // outer pointer ref-id
	0x01, 0x00, 0x00, 0x00, // Entries = 1
	0x14, 0x00, 0x02, 0x00, // Domains pointer ref-id
	0x20, 0x00, 0x00, 0x00, // MaxEntries = 32
	// Domains deferred buffer
	0x01, 0x00, 0x00, 0x00, // conformance count = 1
	// LSAPR_TRUST_INFORMATION[0]: "OLYMPOS"
	0x0e, 0x00, // Length = 14
	0x10, 0x00, // MaximumLength = 16
	0x18, 0x00, 0x02, 0x00, // Name buffer ref-id
	0x1c, 0x00, 0x02, 0x00, // Sid ref-id
	// Deferred for "OLYMPOS" name (max=8, offset=0, actual=7)
	0x08, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x07, 0x00, 0x00, 0x00,
	'O', 0, 'L', 0, 'Y', 0, 'M', 0, 'P', 0, 'O', 0, 'S', 0, 0, 0, // 16 bytes
	// Deferred for OLYMPOS SID (4 sub-authorities)
	0x04, 0x00, 0x00, 0x00, // sub-auth count
	0x01,                                                                                           // revision
	0x04,                                                                                           // sub-auth count again (in SID)
	0x00, 0x00, 0x00, 0x00, 0x00, 0x05,                                                             // 6-byte authority (NT)
	0x15, 0x00, 0x00, 0x00, 0xac, 0x81, 0xbf, 0xa1, 0x9a, 0x18, 0xb4, 0xcd, 0x58, 0x9e, 0x8e, 0x93, // 4 sub-authorities
	// Translated names section (top-level)
	0x03, 0x00, 0x00, 0x00, // nameCount = 3
	0x20, 0x00, 0x02, 0x00, // namesPtr ref-id
	0x03, 0x00, 0x00, 0x00, // conformance count = 3
	// Entry 0: SidType=2 (Group), name "Domain Admins" (length 26)
	0x02, 0x00, // SidType
	0x00, 0x00, // pad
	0x1a, 0x00, // Length
	0x1a, 0x00, // MaxLength
	0x24, 0x00, 0x02, 0x00, // namePtr
	0x00, 0x00, 0x00, 0x00, // domIdx = 0
	0x00, 0x00, 0x00, 0x00, // flags
	// Entry 1: SidType=2 (Group), name "Domain Users" (length 24)
	0x02, 0x00,
	0x00, 0x00,
	0x18, 0x00,
	0x18, 0x00,
	0x28, 0x00, 0x02, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	// Entry 2: SidType=1 (User), name "Share Admin" (length 22)
	0x01, 0x00,
	0x00, 0x00,
	0x16, 0x00,
	0x16, 0x00,
	0x2c, 0x00, 0x02, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	// Deferred for entry 0: "Domain Admins" (13 chars)
	0x0d, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x0d, 0x00, 0x00, 0x00,
	'D', 0, 'o', 0, 'm', 0, 'a', 0, 'i', 0, 'n', 0, ' ', 0, 'A', 0, 'd', 0, 'm', 0, 'i', 0, 'n', 0, 's', 0, // 26 bytes
	0x00, 0x00, // align to 4
	// Deferred for entry 1: "Domain Users" (12 chars)
	0x0c, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x0c, 0x00, 0x00, 0x00,
	'D', 0, 'o', 0, 'm', 0, 'a', 0, 'i', 0, 'n', 0, ' ', 0, 'U', 0, 's', 0, 'e', 0, 'r', 0, 's', 0, // 24 bytes (already aligned)
	// Deferred for entry 2: "Share Admin" (11 chars)
	0x0b, 0x00, 0x00, 0x00,
	0x00, 0x00, 0x00, 0x00,
	0x0b, 0x00, 0x00, 0x00,
	'S', 0, 'h', 0, 'a', 0, 'r', 0, 'e', 0, ' ', 0, 'A', 0, 'd', 0, 'm', 0, 'i', 0, 'n', 0, // 22 bytes
	0x00, 0x00, // align to 4
	// Trailer: mapped_count + status
	0x03, 0x00, 0x00, 0x00, // mapped_count = 3
	0x00, 0x00, 0x00, 0x00, // status = NTSTATUS_SUCCESS
}

func TestParseLookupSids2WithDomains_RealResponse(t *testing.T) {
	if len(realLsaResponseDomain) != 280 {
		t.Fatalf("fixture size mismatch: got %d, want 280", len(realLsaResponseDomain))
	}

	results, status, err := parseLookupSids2WithDomains(realLsaResponseDomain)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if status != 0 {
		t.Errorf("status: want 0 (success), got 0x%x", status)
	}
	if len(results) != 3 {
		t.Fatalf("results: want 3, got %d", len(results))
	}

	want := []struct {
		name    string
		domain  string
		sidType uint16
	}{
		{"Domain Admins", "OLYMPOS", SidTypeGroup},
		{"Domain Users", "OLYMPOS", SidTypeGroup},
		{"Share Admin", "OLYMPOS", SidTypeUser},
	}
	for i, w := range want {
		if results[i].Name != w.name {
			t.Errorf("results[%d].Name: want %q, got %q", i, w.name, results[i].Name)
		}
		if results[i].Domain != w.domain {
			t.Errorf("results[%d].Domain: want %q, got %q", i, w.domain, results[i].Domain)
		}
		if results[i].SidType != w.sidType {
			t.Errorf("results[%d].SidType: want %d, got %d", i, w.sidType, results[i].SidType)
		}
	}
}
