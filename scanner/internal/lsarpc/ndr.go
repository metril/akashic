package lsarpc

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

// Pad4 returns the number of zero-pad bytes needed to align `n` to 4 bytes.
func Pad4(n int) int { return (4 - n%4) % 4 }

// EncodeRPCUnicodeString encodes an RPC_UNICODE_STRING (MS-DTYP §2.3.10).
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
