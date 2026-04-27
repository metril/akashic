package lsarpc

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestSkipDomains_ZeroEntries(t *testing.T) {
	var buf bytes.Buffer
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // entries
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020010)) // domains ptr
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // max count
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // MaxEntries

	r := newReader(buf.Bytes())
	r.SkipDomains()
	if r.pos != 16 {
		t.Errorf("expected pos 16 after empty SkipDomains, got %d", r.pos)
	}
}

func TestSkipDomains_OneEntry(t *testing.T) {
	var buf bytes.Buffer
	binary.Write(&buf, binary.LittleEndian, uint32(1))          // entries
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020010)) // domains ptr
	binary.Write(&buf, binary.LittleEndian, uint32(1))          // max count
	// Entry 0 fixed-part:
	binary.Write(&buf, binary.LittleEndian, uint16(8))          // length (4 chars * 2)
	binary.Write(&buf, binary.LittleEndian, uint16(8))          // maxLen
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020014)) // namePtr (non-zero)
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020018)) // sidPtr (non-zero)
	// Deferred name: max_count, offset, actual_count, chars, align
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // max_count
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // offset
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // actual
	buf.Write([]byte{'D', 0, 'O', 0, 'M', 0, '.', 0})           // 8 bytes — already aligned
	// Deferred SID: max_count + SID body + align
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // sub count
	buf.WriteByte(1)                                             // revision
	buf.WriteByte(4)                                             // sub auth count
	buf.Write([]byte{0, 0, 0, 0, 0, 5})                         // identifier authority (BE)
	for i := 0; i < 4; i++ {
		binary.Write(&buf, binary.LittleEndian, uint32(21+uint32(i)))
	}
	// SID is 24 bytes — 24 mod 4 == 0, no align needed
	binary.Write(&buf, binary.LittleEndian, uint32(1)) // MaxEntries

	r := newReader(buf.Bytes())
	r.SkipDomains()
	if r.pos != len(buf.Bytes()) {
		t.Errorf("expected pos %d after SkipDomains (consumed all), got %d", len(buf.Bytes()), r.pos)
	}
}
