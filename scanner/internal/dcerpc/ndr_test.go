package dcerpc

import "testing"

func TestEncodeRPCUnicodeString_HeaderAndDeferred(t *testing.T) {
	// Encode "AB" (4 UTF-16LE bytes).
	got := EncodeRPCUnicodeString("AB", 0x20000)
	// Header (8 bytes): length=4, max=4, referent=0x20000
	if got[0] != 4 || got[1] != 0 || got[2] != 4 || got[3] != 0 {
		t.Errorf("header lengths wrong: % x", got[:4])
	}
	if got[4] != 0x00 || got[5] != 0x00 || got[6] != 0x02 || got[7] != 0x00 {
		t.Errorf("referent wrong: % x", got[4:8])
	}
	// Deferred: max=2, offset=0, actual=2, then "AB" UTF-16LE.
	if got[8] != 2 || got[12] != 0 || got[16] != 2 {
		t.Errorf("deferred counts wrong: max=%d offset=%d actual=%d", got[8], got[12], got[16])
	}
	if got[20] != 'A' || got[21] != 0 || got[22] != 'B' || got[23] != 0 {
		t.Errorf("string bytes wrong: % x", got[20:24])
	}
}

func TestUTF16RoundTrip(t *testing.T) {
	in := "héllo"
	dec := DecodeUTF16LE(EncodeUTF16LE(in))
	if dec != in {
		t.Fatalf("round-trip: got %q want %q", dec, in)
	}
}

func TestPad4(t *testing.T) {
	cases := map[int]int{0: 0, 1: 3, 2: 2, 3: 1, 4: 0, 5: 3}
	for in, want := range cases {
		if got := Pad4(in); got != want {
			t.Errorf("Pad4(%d) = %d, want %d", in, got, want)
		}
	}
}

func TestAlignBytes(t *testing.T) {
	if got := AlignBytes([]byte{1, 2, 3}, 4); len(got) != 4 || got[3] != 0 {
		t.Fatalf("AlignBytes: % x", got)
	}
	if got := AlignBytes([]byte{1, 2, 3, 4}, 4); len(got) != 4 {
		t.Fatalf("AlignBytes already-aligned: % x", got)
	}
}
