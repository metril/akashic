package samr

import (
	"bytes"
	"testing"
)

func TestBuildBindRequest_SAMR(t *testing.T) {
	pkt := BuildBindRequest(1, 4280, 4280)
	if len(pkt) != 72 {
		t.Fatalf("bind PDU len = %d, want 72", len(pkt))
	}
	// Common DCE/RPC bind prefix: rpc_vers=5, vers_minor=0, ptype=11 (Bind),
	// flags=0x03 (FirstFrag|LastFrag), drep=0x10 0x00 0x00 0x00.
	wantPrefix := []byte{0x05, 0x00, 0x0b, 0x03, 0x10, 0x00, 0x00, 0x00}
	if !bytes.Equal(pkt[:8], wantPrefix) {
		t.Errorf("PDU prefix wrong:\n got: % x\nwant: % x", pkt[:8], wantPrefix)
	}
	// SAMR UUID lives at offset 16 (PDU header) + 8 (max_frag/assoc) + 8 (ctx hdr) = 32.
	uuidStart := 16 + 8 + 8
	if !bytes.Equal(pkt[uuidStart:uuidStart+16], samrUUID[:]) {
		t.Errorf("SAMR UUID wrong at offset %d:\n got: % x\nwant: % x",
			uuidStart, pkt[uuidStart:uuidStart+16], samrUUID[:])
	}
	// Version 1 follows the UUID (uint16 vers_major + uint16 vers_minor).
	verOff := uuidStart + 16
	if pkt[verOff] != 1 || pkt[verOff+1] != 0 {
		t.Errorf("SAMR version = %d.%d, want 1.0", pkt[verOff], pkt[verOff+1])
	}
}
