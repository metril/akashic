package samr

import (
	"encoding/binary"
	"fmt"
	"unicode/utf16"
)

// EncodeUTF16LE returns the UTF-16 little-endian byte sequence for s,
// without a trailing null.
func EncodeUTF16LE(s string) []byte {
	codes := utf16.Encode([]rune(s))
	out := make([]byte, len(codes)*2)
	for i, c := range codes {
		binary.LittleEndian.PutUint16(out[i*2:], c)
	}
	return out
}

// DecodeUTF16LE inverts EncodeUTF16LE.
func DecodeUTF16LE(b []byte) string {
	if len(b)%2 != 0 {
		return ""
	}
	codes := make([]uint16, len(b)/2)
	for i := range codes {
		codes[i] = binary.LittleEndian.Uint16(b[i*2 : i*2+2])
	}
	return string(decodeUTF16(codes))
}

// Pad4 returns the number of zero-pad bytes needed to align n to 4 bytes.
func Pad4(n int) int { return (4 - n%4) % 4 }

// EncodeRPCUnicodeString encodes an RPC_UNICODE_STRING (MS-DTYP §2.3.10)
// inline + deferred payload. Used for SAMR server names, account names,
// etc. The header is:
//
//	uint16 Length        (in bytes, no NUL)
//	uint16 MaximumLength (in bytes)
//	uint32 Buffer ptr    (referent ID)
//
// Followed by the deferred buffer:
//
//	uint32 max_count
//	uint32 offset (0)
//	uint32 actual_count (chars)
//	UTF-16LE chars
//	pad to 4
func EncodeRPCUnicodeString(s string, referentID uint32) []byte {
	codes := utf16.Encode([]rune(s))
	byteLen := len(codes) * 2
	header := make([]byte, 8)
	binary.LittleEndian.PutUint16(header[0:2], uint16(byteLen))
	binary.LittleEndian.PutUint16(header[2:4], uint16(byteLen))
	binary.LittleEndian.PutUint32(header[4:8], referentID)

	body := make([]byte, 12+byteLen)
	binary.LittleEndian.PutUint32(body[0:4], uint32(len(codes)))
	binary.LittleEndian.PutUint32(body[4:8], 0)
	binary.LittleEndian.PutUint32(body[8:12], uint32(len(codes)))
	for i, c := range codes {
		binary.LittleEndian.PutUint16(body[12+i*2:], c)
	}
	if pad := Pad4(len(body)); pad > 0 {
		body = append(body, make([]byte, pad)...)
	}
	return append(header, body...)
}

// EncodeRPCSID encodes an RPC_SID (MS-DTYP §2.4.2.3) preceded by its NDR
// conformance count, suitable for embedding in a request body.
//
// Wire format:
//
//	uint32 conformance_count = SubAuthCount
//	uint8  Revision (1)
//	uint8  SubAuthCount
//	[6]byte IdentifierAuthority   (big-endian per MS-DTYP §2.4.2.1)
//	[N]uint32 SubAuthority        (little-endian)
//
// Padded to 4-byte boundary at the end.
//
// Note: the IdentifierAuthority is six bytes interpreted as a 48-bit big-endian
// integer; this is the only big-endian field in a SID. Sub-authorities follow
// in little-endian.
func EncodeRPCSID(sid SID) []byte {
	subCount := len(sid.SubAuthority)
	out := make([]byte, 0, 12+subCount*4)
	out = binary.LittleEndian.AppendUint32(out, uint32(subCount))
	out = append(out, sid.Revision)
	out = append(out, byte(subCount))
	out = append(out, sid.Authority[:]...)
	for _, sa := range sid.SubAuthority {
		out = binary.LittleEndian.AppendUint32(out, sa)
	}
	if pad := Pad4(len(out)); pad > 0 {
		out = append(out, make([]byte, pad)...)
	}
	return out
}

// AlignBytes appends zero bytes to b so that len(b) is aligned to n.
func AlignBytes(b []byte, n int) []byte {
	if pad := (n - len(b)%n) % n; pad > 0 {
		return append(b, make([]byte, pad)...)
	}
	return b
}

// DecodeRPCSID parses bytes produced by EncodeRPCSID (or the wire form of
// RPC_SID with leading conformance count). Used by GetGroupsForUser
// response parsing if needed (currently we only emit, don't parse, SIDs).
func DecodeRPCSID(b []byte) (SID, int, error) {
	if len(b) < 12 {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid too short")
	}
	conformance := binary.LittleEndian.Uint32(b[0:4])
	revision := b[4]
	subCount := b[5]
	if uint32(subCount) != conformance {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid conformance mismatch (%d vs %d)", conformance, subCount)
	}
	if len(b) < 8+int(subCount)*4+4 {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid truncated")
	}
	var auth [6]byte
	copy(auth[:], b[6:12])
	subs := make([]uint32, subCount)
	for i := 0; i < int(subCount); i++ {
		subs[i] = binary.LittleEndian.Uint32(b[12+i*4 : 16+i*4])
	}
	consumed := 12 + int(subCount)*4
	consumed += Pad4(consumed)
	return SID{Revision: revision, Authority: auth, SubAuthority: subs}, consumed, nil
}
