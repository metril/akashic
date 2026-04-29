package samr

import (
	"bytes"
	"testing"
)

func TestEncodeRPCSID_KnownDomainFixture(t *testing.T) {
	// MS-DTYP §2.4.2.1 example domain SID with 4 sub-authorities (revision=1,
	// authority=NT (5), sub-auths 21, 1004336348, 1177238915, 682003330).
	// Final wire form (per [MS-DTYP] §2.4.2.3, RPC_SID NDR):
	//   uint32 conformance count = sub-auth count = 4
	//   uint8  Revision = 1
	//   uint8  SubAuthCount = 4
	//   [6]byte IdentifierAuthority = 00 00 00 00 00 05
	//   uint32 SubAuthority[0] = 21        → 15 00 00 00
	//   uint32 SubAuthority[1] = 1004336348 → dc f4 dc 3b
	//   uint32 SubAuthority[2] = 1177238915 → 83 3d 2b 46
	//   uint32 SubAuthority[3] = 682003330  → 82 8b a6 28
	// 28 bytes total; 4-aligned, no pad.
	sid, err := ParseSidString("S-1-5-21-1004336348-1177238915-682003330")
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	want := []byte{
		0x04, 0x00, 0x00, 0x00, // conformance count = 4
		0x01,                   // revision
		0x04,                   // sub-auth count
		0x00, 0x00, 0x00, 0x00, 0x00, 0x05, // authority (BE)
		0x15, 0x00, 0x00, 0x00, // 21
		0xdc, 0xf4, 0xdc, 0x3b, // 1004336348 = 0x3BDCF4DC
		0x83, 0x3d, 0x2b, 0x46, // 1177238915 = 0x462B3D83
		0x82, 0x8b, 0xa6, 0x28, // 682003330  = 0x28A68B82
	}
	got := EncodeRPCSID(sid)
	if !bytes.Equal(got, want) {
		t.Fatalf("EncodeRPCSID:\n got: % x\nwant: % x", got, want)
	}
}

func TestEncodeRPCSID_PadsToFourBytes(t *testing.T) {
	// SID with 0 sub-authorities encodes to: 4 (count) + 1 (rev) + 1 (count) + 6 (auth) = 12 bytes.
	// Already 4-aligned; no padding.
	s := SID{Revision: 1, Authority: [6]byte{0, 0, 0, 0, 0, 5}}
	got := EncodeRPCSID(s)
	if len(got) != 12 {
		t.Fatalf("len = %d, want 12", len(got))
	}
	if got[0] != 0 || got[1] != 0 || got[2] != 0 || got[3] != 0 {
		t.Errorf("conformance bytes wrong: % x", got[:4])
	}
}

func TestDecodeRPCSID_RoundTrip(t *testing.T) {
	in, _ := ParseSidString("S-1-5-21-1004336348-1177238915-682003330")
	enc := EncodeRPCSID(in)
	out, _, err := DecodeRPCSID(enc)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if out.String() != in.String() {
		t.Fatalf("round-trip mismatch: %q vs %q", out.String(), in.String())
	}
}
