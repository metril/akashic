package sddl

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestParseSID_System(t *testing.T) {
	var buf bytes.Buffer
	buf.WriteByte(1)
	buf.WriteByte(1)
	buf.Write([]byte{0, 0, 0, 0, 0, 5})
	binary.Write(&buf, binary.LittleEndian, uint32(18))

	got, n, err := ParseSID(buf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if got != "S-1-5-18" {
		t.Errorf("got %q want S-1-5-18", got)
	}
	if n != 12 {
		t.Errorf("got n=%d want 12", n)
	}
}

func TestParseSID_DomainUser(t *testing.T) {
	var buf bytes.Buffer
	buf.WriteByte(1)
	buf.WriteByte(5)
	buf.Write([]byte{0, 0, 0, 0, 0, 5})
	for _, sub := range []uint32{21, 100, 200, 300, 1013} {
		binary.Write(&buf, binary.LittleEndian, sub)
	}

	got, n, err := ParseSID(buf.Bytes())
	if err != nil {
		t.Fatal(err)
	}
	if got != "S-1-5-21-100-200-300-1013" {
		t.Errorf("got %q", got)
	}
	if n != 28 {
		t.Errorf("got n=%d want 28", n)
	}
}

func TestParseSID_TruncatedRejected(t *testing.T) {
	if _, _, err := ParseSID([]byte{1, 5, 0, 0}); err == nil {
		t.Error("expected error on truncated SID")
	}
}
