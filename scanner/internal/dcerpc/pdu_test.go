package dcerpc

import (
	"bytes"
	"testing"
)

func TestPDUHeader_RoundTrip(t *testing.T) {
	hdr := PDUHeader{
		PType:   PtypeRequest,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: 100,
		AuthLen: 0,
		CallID:  42,
	}
	b := hdr.Marshal()
	if len(b) != 16 {
		t.Fatalf("marshal len = %d, want 16", len(b))
	}
	got, err := ParsePDUHeader(b)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if got != hdr {
		t.Fatalf("round-trip mismatch: %+v vs %+v", got, hdr)
	}
}

func TestParsePDUHeader_Truncated(t *testing.T) {
	if _, err := ParsePDUHeader([]byte{1, 2, 3}); err == nil {
		t.Fatal("expected error on short input")
	}
}

func TestEncodePDUHeader_BindBytes(t *testing.T) {
	hdr := PDUHeader{
		PType:   PtypeBind,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: 72,
		AuthLen: 0,
		CallID:  1,
	}
	got := hdr.Marshal()
	want := []byte{
		5, 0, byte(PtypeBind), 3,
		0x10, 0x00, 0x00, 0x00,
		72, 0,
		0, 0,
		1, 0, 0, 0,
	}
	if !bytes.Equal(got, want) {
		t.Errorf("\ngot  %x\nwant %x", got, want)
	}
}
