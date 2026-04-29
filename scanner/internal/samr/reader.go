package samr

import (
	"encoding/binary"
	"unicode/utf16"
)

type reader struct {
	b   []byte
	pos int
}

func newReader(b []byte) *reader { return &reader{b: b} }

func (r *reader) U16() uint16 {
	if r.pos+2 > len(r.b) {
		return 0
	}
	v := binary.LittleEndian.Uint16(r.b[r.pos:])
	r.pos += 2
	return v
}

func (r *reader) U32() uint32 {
	if r.pos+4 > len(r.b) {
		return 0
	}
	v := binary.LittleEndian.Uint32(r.b[r.pos:])
	r.pos += 4
	return v
}

func (r *reader) Bytes(n int) []byte {
	if r.pos+n > len(r.b) {
		return nil
	}
	v := r.b[r.pos : r.pos+n]
	r.pos += n
	return v
}

func (r *reader) AlignTo(n int) {
	pad := (n - r.pos%n) % n
	r.pos += pad
}

func (r *reader) Tail32() uint32 {
	if len(r.b) < 4 {
		return 0
	}
	return binary.LittleEndian.Uint32(r.b[len(r.b)-4:])
}

func decodeUTF16(codes []uint16) []rune { return utf16.Decode(codes) }
