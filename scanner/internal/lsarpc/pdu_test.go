package lsarpc

import (
	"bytes"
	"testing"
)

func TestEncodePDUHeader_Bind(t *testing.T) {
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
