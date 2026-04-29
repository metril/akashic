package dcerpc

import (
	"encoding/binary"
	"unicode/utf16"
)

// Reader is a positional byte reader for parsing NDR-encoded response
// bodies. Bounds-violating reads return zero values rather than panic;
// callers that care about under-read can compare positions against
// expectations after the parse.
type Reader struct {
	b   []byte
	pos int
}

func NewReader(b []byte) *Reader { return &Reader{b: b} }

// Pos returns the current read position. Useful in tests that need to
// assert how many bytes a parser consumed.
func (r *Reader) Pos() int { return r.pos }

func (r *Reader) U16() uint16 {
	if r.pos+2 > len(r.b) {
		return 0
	}
	v := binary.LittleEndian.Uint16(r.b[r.pos:])
	r.pos += 2
	return v
}

func (r *Reader) U32() uint32 {
	if r.pos+4 > len(r.b) {
		return 0
	}
	v := binary.LittleEndian.Uint32(r.b[r.pos:])
	r.pos += 4
	return v
}

func (r *Reader) Bytes(n int) []byte {
	if r.pos+n > len(r.b) {
		return nil
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v
}

func (r *Reader) AlignTo(n int) {
	pad := (n - r.pos%n) % n
	r.pos += pad
}

// Tail32 returns the last 4 bytes of the underlying slice as a
// little-endian uint32 — typically the trailing NTSTATUS in a SAMR/LSARPC
// response. Does not advance the reader position.
func (r *Reader) Tail32() uint32 {
	if len(r.b) < 4 {
		return 0
	}
	return binary.LittleEndian.Uint32(r.b[len(r.b)-4:])
}

// DecodeUTF16Codes is a thin re-export of the stdlib helper, exported so
// callers in per-binding packages can decode UTF-16 chunks without
// importing unicode/utf16 directly.
func DecodeUTF16Codes(codes []uint16) []rune { return utf16.Decode(codes) }
