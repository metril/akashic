package nfsprobe

import (
	"bytes"
	"testing"
)

// XDR primitive round-trips. Wire bytes verified against RFC 4506
// examples — uint32 is 4 bytes big-endian, opaque is length-prefixed +
// padded to 4-byte alignment.

func TestXDRWriteUint32(t *testing.T) {
	w := newXDRWriter()
	w.writeUint32(0xDEADBEEF)
	got := w.bytes()
	want := []byte{0xDE, 0xAD, 0xBE, 0xEF}
	if !bytes.Equal(got, want) {
		t.Errorf("got %x, want %x", got, want)
	}
}

func TestXDRWriteString(t *testing.T) {
	// "abc" → length 3, "abc", 1 byte pad to align.
	w := newXDRWriter()
	w.writeString("abc")
	got := w.bytes()
	want := []byte{0, 0, 0, 3, 'a', 'b', 'c', 0}
	if !bytes.Equal(got, want) {
		t.Errorf("got %x, want %x", got, want)
	}
}

func TestXDRWriteString4ByteAligned(t *testing.T) {
	// "abcd" → length 4, "abcd", 0 pad.
	w := newXDRWriter()
	w.writeString("abcd")
	got := w.bytes()
	want := []byte{0, 0, 0, 4, 'a', 'b', 'c', 'd'}
	if !bytes.Equal(got, want) {
		t.Errorf("got %x, want %x", got, want)
	}
}

func TestXDRWriteEmptyString(t *testing.T) {
	// Empty string is just length 0, no body, no pad.
	w := newXDRWriter()
	w.writeString("")
	want := []byte{0, 0, 0, 0}
	if !bytes.Equal(w.bytes(), want) {
		t.Errorf("got %x, want %x", w.bytes(), want)
	}
}

func TestXDRReadUint32(t *testing.T) {
	r := newXDRReader([]byte{0xDE, 0xAD, 0xBE, 0xEF})
	v, err := r.readUint32()
	if err != nil {
		t.Fatal(err)
	}
	if v != 0xDEADBEEF {
		t.Errorf("got %x, want DEADBEEF", v)
	}
}

func TestXDRReadString(t *testing.T) {
	r := newXDRReader([]byte{0, 0, 0, 3, 'a', 'b', 'c', 0})
	s, err := r.readString()
	if err != nil {
		t.Fatal(err)
	}
	if s != "abc" {
		t.Errorf("got %q, want abc", s)
	}
}

func TestXDRReadShortBufferReportsContext(t *testing.T) {
	r := newXDRReader([]byte{0, 0})
	_, err := r.readUint32()
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestXDRReadOpaqueRejectsAbsurdLength(t *testing.T) {
	// length = 32 MB, which exceeds our 16 MB cap.
	r := newXDRReader([]byte{0x02, 0, 0, 0})
	_, err := r.readOpaque()
	if err == nil {
		t.Fatal("expected oversized opaque to be rejected")
	}
}

func TestXDRWriteUint32List(t *testing.T) {
	w := newXDRWriter()
	w.writeUint32List([]uint32{1, 2, 3})
	want := []byte{
		0, 0, 0, 3, // count
		0, 0, 0, 1,
		0, 0, 0, 2,
		0, 0, 0, 3,
	}
	if !bytes.Equal(w.bytes(), want) {
		t.Errorf("got %x, want %x", w.bytes(), want)
	}
}
