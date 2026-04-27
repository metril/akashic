package lsarpc

import (
	"encoding/binary"
	"testing"
)

func TestBuildLookupSidsRequest_OpnumAndCount(t *testing.T) {
	var h PolicyHandle
	for i := range h {
		h[i] = byte(i)
	}
	sids := [][]byte{
		{1, 1, 0, 0, 0, 0, 0, 5, 18, 0, 0, 0},
		{1, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0},
	}
	pkt, err := BuildLookupSids2Request(3, h, sids)
	if err != nil {
		t.Fatal(err)
	}
	hdr, _ := ParsePDUHeader(pkt)
	if hdr.PType != PtypeRequest {
		t.Errorf("ptype")
	}
	body := pkt[16:]
	opnum := binary.LittleEndian.Uint16(body[6:8])
	if opnum != OpnumLsarLookupSids2 {
		t.Errorf("opnum: got %d want %d", opnum, OpnumLsarLookupSids2)
	}
}
