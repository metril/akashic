package lsarpc

import (
	"bytes"
	"testing"
)

func TestBuildBindRequest_LSARPC(t *testing.T) {
	pkt := BuildBindRequest(1, 4280, 4280)
	hdr, err := ParsePDUHeader(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if hdr.PType != PtypeBind {
		t.Errorf("ptype: got %d want %d", hdr.PType, PtypeBind)
	}
	if hdr.CallID != 1 {
		t.Errorf("call_id: got %d want 1", hdr.CallID)
	}
	if int(hdr.FragLen) != len(pkt) {
		t.Errorf("frag_len %d != packet length %d", hdr.FragLen, len(pkt))
	}
	wantUUID := []byte{
		0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
		0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab,
	}
	if !bytes.Contains(pkt, wantUUID) {
		t.Errorf("LSARPC interface UUID not found in bind packet")
	}
}
