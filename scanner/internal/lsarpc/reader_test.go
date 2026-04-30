package lsarpc

import (
	"bytes"
	"encoding/binary"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// Wire layout these tests assume — see readDomainsTable in
// lookup_with_domains.go for the field-by-field annotation. The earlier
// version of these tests was written against the buggy layout (treating
// MaxEntries as a trailing footer and missing the deferred conformance
// count); they pass against a real captured response now.

func TestSkipDomains_ZeroEntries(t *testing.T) {
	var buf bytes.Buffer
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // entries
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020010)) // Domains ref-id
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // MaxEntries
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // deferred conformance count

	r := dcerpc.NewReader(buf.Bytes())
	skipDomains(r)
	if r.Pos() != 16 {
		t.Errorf("expected pos 16 after empty SkipDomains, got %d", r.Pos())
	}
}

func TestSkipDomains_OneEntry(t *testing.T) {
	var buf bytes.Buffer
	binary.Write(&buf, binary.LittleEndian, uint32(1))          // entries
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020010)) // Domains ref-id
	binary.Write(&buf, binary.LittleEndian, uint32(1))          // MaxEntries
	binary.Write(&buf, binary.LittleEndian, uint32(1))          // deferred conformance count
	// Entry 0 fixed-part: 12 bytes
	binary.Write(&buf, binary.LittleEndian, uint16(8))          // Length (4 chars * 2)
	binary.Write(&buf, binary.LittleEndian, uint16(8))          // MaxLength
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020014)) // Name buffer ref-id (non-zero)
	binary.Write(&buf, binary.LittleEndian, uint32(0x00020018)) // Sid ref-id (non-zero)
	// Deferred name: max_count, offset, actual_count, chars, align
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // max_count
	binary.Write(&buf, binary.LittleEndian, uint32(0))          // offset
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // actual_count
	buf.Write([]byte{'D', 0, 'O', 0, 'M', 0, '.', 0})           // 8 bytes — already aligned
	// Deferred SID: max_count + SID body + align
	binary.Write(&buf, binary.LittleEndian, uint32(4))          // sub-auth count
	buf.WriteByte(1)                                            // revision
	buf.WriteByte(4)                                            // sub-auth count again (in SID)
	buf.Write([]byte{0, 0, 0, 0, 0, 5})                         // identifier authority (BE)
	for i := 0; i < 4; i++ {
		binary.Write(&buf, binary.LittleEndian, uint32(21+uint32(i)))
	}
	// SID is 24 bytes — 24 mod 4 == 0, no align needed.
	// No trailing MaxEntries footer — it's the third top-level field
	// (already consumed at byte 8).

	r := dcerpc.NewReader(buf.Bytes())
	skipDomains(r)
	if r.Pos() != len(buf.Bytes()) {
		t.Errorf("expected pos %d after SkipDomains (consumed all), got %d", len(buf.Bytes()), r.Pos())
	}
}
