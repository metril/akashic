package dcerpc

import (
	"encoding/binary"
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
	return string(DecodeUTF16Codes(codes))
}

// Pad4 returns the number of zero-pad bytes needed to align n to 4 bytes.
func Pad4(n int) int { return (4 - n%4) % 4 }

// AlignBytes appends zero bytes to b so that len(b) is aligned to n.
func AlignBytes(b []byte, n int) []byte {
	if pad := (n - len(b)%n) % n; pad > 0 {
		return append(b, make([]byte, pad)...)
	}
	return b
}

// EncodeRPCUnicodeString encodes an RPC_UNICODE_STRING (MS-DTYP §2.3.10)
// inline + deferred payload. Used by both LSARPC and SAMR. The header is:
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
