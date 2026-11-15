package lsarpc

import (
	"bytes"
	"encoding/binary"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

var testDomSid = []byte{1, 4, 0, 0, 0, 0, 0, 5, 0x15, 0, 0, 0, 0xac, 0x81, 0xbf, 0xa1, 0x9a, 0x18, 0xb4, 0xcd, 0x58, 0x9e, 0x8e, 0x93}

func u32(v uint32) []byte { return binary.LittleEndian.AppendUint32(nil, v) }

// namesResponse builds a response: one domain "OLYMPOS", then one entry per
// sidTypes element (sid != nil -> mapped user with RID 1104).
func namesResponse(mapped []bool, status uint32) []byte {
	var b []byte
	b = append(b, u32(0x20000)...)
	b = append(b, u32(1)...)
	b = append(b, u32(0x20004)...)
	b = append(b, u32(32)...)
	b = append(b, u32(1)...)
	b = append(b, 0x0e, 0, 0x10, 0)
	b = append(b, u32(0x20008)...)
	b = append(b, u32(0x2000c)...)
	b = append(b, u32(8)...)
	b = append(b, u32(0)...)
	b = append(b, u32(7)...)
	b = append(b, dcerpc.EncodeUTF16LE("OLYMPOS")...)
	b = append(b, 0, 0)
	b = append(b, u32(4)...)
	b = append(b, testDomSid...)

	b = append(b, u32(uint32(len(mapped)))...)
	b = append(b, u32(0x20010)...)
	b = append(b, u32(uint32(len(mapped)))...)
	for i, m := range mapped {
		if m {
			b = append(b, 1, 0, 0, 0)
			b = append(b, u32(0x20014+uint32(i)*4)...)
			b = append(b, u32(0)...)
		} else {
			b = append(b, 8, 0, 0, 0)
			b = append(b, u32(0)...)
			b = append(b, u32(0xffffffff)...)
		}
		b = append(b, u32(0)...)
	}
	n := 0
	for _, m := range mapped {
		if m {
			b = append(b, u32(5)...)
			b = append(b, 1, 5)
			b = append(b, testDomSid[2:]...)
			b = append(b, u32(1104)...)
			n++
		}
	}
	b = append(b, u32(uint32(n))...)
	b = append(b, u32(status)...)
	return b
}

func TestParseLookupNames3Mapped(t *testing.T) {
	res, st, err := ParseLookupNames3Response(namesResponse([]bool{true}, 0))
	if err != nil || st != 0 || len(res) != 1 {
		t.Fatalf("err=%v st=%x res=%v", err, st, res)
	}
	r := res[0]
	if !r.Mapped || r.SidString != "S-1-5-21-2713682348-3451132058-2475597400-1104" {
		t.Fatalf("bad %+v", r)
	}
	if r.Domain != "OLYMPOS" || r.DomainSid != "S-1-5-21-2713682348-3451132058-2475597400" {
		t.Fatalf("bad domain %+v", r)
	}
	if r.SidType != SidTypeUser {
		t.Fatalf("type %d", r.SidType)
	}
}

func TestParseLookupNames3SomeNotMapped(t *testing.T) {
	res, st, err := ParseLookupNames3Response(namesResponse([]bool{true, false}, StatusSomeNotMapped))
	if err != nil || st != StatusSomeNotMapped || len(res) != 2 {
		t.Fatalf("err=%v st=%x res=%v", err, st, res)
	}
	if !res[0].Mapped || res[1].Mapped || res[1].Sid != nil || res[1].Domain != "" {
		t.Fatalf("bad %+v", res)
	}
}

func TestParseLookupNames3NoneMapped(t *testing.T) {
	res, st, err := ParseLookupNames3Response(namesResponse([]bool{false}, StatusNoneMapped))
	if err != nil || st != StatusNoneMapped || len(res) != 1 || res[0].Mapped {
		t.Fatalf("err=%v st=%x res=%v", err, st, res)
	}
	// NULL domain list + NULL sids: just ptr,0,ptr(0),... status.
	body := append(append(append(u32(0), u32(0)...), u32(0)...), u32(0)...)
	body = append(body, u32(0)...)
	body = append(body, u32(StatusNoneMapped)...)
	res, st, err = ParseLookupNames3Response(body)
	if err != nil || st != StatusNoneMapped || len(res) != 0 {
		t.Fatalf("err=%v st=%x res=%v", err, st, res)
	}
}

func TestParseLookupNames3Truncated(t *testing.T) {
	full := namesResponse([]bool{true}, 0)
	if _, _, err := ParseLookupNames3Response(full[:3]); err == nil {
		t.Fatal("expected error")
	}
	if _, _, err := ParseLookupNames3Response(full[:len(full)-12]); err == nil {
		t.Fatal("expected error on truncated sid")
	}
}

func TestBuildLookupNames3Request(t *testing.T) {
	var h PolicyHandle
	h[0] = 0xab
	pkt, err := BuildLookupNames3Request(7, h, []string{`OLYMPOS\jdoe`, "al"})
	if err != nil {
		t.Fatal(err)
	}
	body := pkt[24:]
	if pkt[2] != 0 || binary.LittleEndian.Uint16(pkt[22:24]) != OpnumLsarLookupNames3 {
		t.Fatalf("opnum wrong: %x", pkt[:24])
	}
	if body[0] != 0xab || binary.LittleEndian.Uint32(body[20:]) != 2 || binary.LittleEndian.Uint32(body[24:]) != 2 {
		t.Fatalf("header wrong: %x", body[:32])
	}
	// first RPC_UNICODE_STRING: len = 12 chars * 2
	if binary.LittleEndian.Uint16(body[28:]) != 24 {
		t.Fatalf("len wrong")
	}
	// tail: Entries=0, ptr=0, level=1, pad, mapped=0, opts=0, rev=2
	want := append(append(append(u32(0), u32(0)...), 1, 0, 0, 0), append(append(u32(0), u32(0)...), u32(2)...)...)
	if !bytes.HasSuffix(body, want) {
		t.Fatalf("tail wrong: %x", body[len(body)-24:])
	}
	if len(body)%4 != 0 {
		t.Fatalf("unaligned body: %d", len(body))
	}
	if _, err := BuildLookupNames3Request(1, h, nil); err == nil {
		t.Fatal("expected error for no names")
	}
	if _, err := BuildLookupNames3Request(1, h, []string{""}); err == nil {
		t.Fatal("expected error for empty name")
	}
}

func TestSidBytesToString(t *testing.T) {
	if got := SidBytesToString([]byte{1, 1, 0, 0, 0, 0, 0, 5, 0x20, 2, 0, 0}); got != "S-1-5-544" {
		t.Fatal(got)
	}
	if SidBytesToString([]byte{1, 2, 0, 0, 0, 0, 0, 5}) != "" {
		t.Fatal("expected empty on short")
	}
}
