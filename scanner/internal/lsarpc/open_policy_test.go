package lsarpc

import (
	"bytes"
	"encoding/binary"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

func TestBuildOpenPolicy2Request(t *testing.T) {
	pkt := BuildOpenPolicy2Request(2, 0x02000000)
	hdr, err := dcerpc.ParsePDUHeader(pkt)
	if err != nil {
		t.Fatal(err)
	}
	if hdr.PType != dcerpc.PtypeRequest {
		t.Errorf("ptype: got %d want REQUEST", hdr.PType)
	}
	body := pkt[16:]
	if len(body) < 8 {
		t.Fatal("body too short")
	}
	opnum := binary.LittleEndian.Uint16(body[6:8])
	if opnum != 44 {
		t.Errorf("opnum: got %d want 44", opnum)
	}
}

func TestParseOpenPolicy2Response(t *testing.T) {
	body := bytes.Repeat([]byte{0xAB}, 20)
	body = append(body, 0, 0, 0, 0)
	handle, status, err := ParseOpenPolicy2Response(body)
	if err != nil {
		t.Fatal(err)
	}
	if status != 0 {
		t.Errorf("status: got %x", status)
	}
	if !bytes.Equal(handle[:], bytes.Repeat([]byte{0xAB}, 20)) {
		t.Errorf("handle mismatch: %x", handle)
	}
}
