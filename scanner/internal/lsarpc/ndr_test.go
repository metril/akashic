package lsarpc

import (
	"bytes"
	"testing"
)

func TestEncodeRPCUnicodeString_Inline(t *testing.T) {
	got := EncodeRPCUnicodeString("x", 0x00020000)
	if len(got) < 8 {
		t.Fatalf("encoded too short: %x", got)
	}
	want := []byte{0x02, 0x00, 0x02, 0x00, 0x00, 0x00, 0x02, 0x00}
	if !bytes.Equal(got[:8], want) {
		t.Errorf("\nstring header got %x\nwant %x", got[:8], want)
	}
}

func TestEncodeUTF16LE(t *testing.T) {
	got := EncodeUTF16LE("ab")
	want := []byte{'a', 0, 'b', 0}
	if !bytes.Equal(got, want) {
		t.Errorf("got %x want %x", got, want)
	}
}

func TestPad4(t *testing.T) {
	cases := map[int]int{0: 0, 1: 3, 2: 2, 3: 1, 4: 0, 5: 3}
	for in, want := range cases {
		if got := Pad4(in); got != want {
			t.Errorf("pad4(%d): got %d want %d", in, got, want)
		}
	}
}
