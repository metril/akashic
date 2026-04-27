package lsarpc

import (
	"encoding/binary"
	"fmt"
)

// TranslatedName holds the LSARPC name resolution for one input SID.
type TranslatedName struct {
	SidType   uint16
	Name      string
	DomainIdx int32
}

// BuildLookupSids2Request encodes the input side of LsarLookupSids2.
func BuildLookupSids2Request(callID uint32, h PolicyHandle, sids [][]byte) ([]byte, error) {
	body := make([]byte, 0, 64+len(sids)*32)
	body = append(body, h[:]...)

	body = binary.LittleEndian.AppendUint32(body, uint32(len(sids)))
	body = binary.LittleEndian.AppendUint32(body, 0x00020000)
	body = binary.LittleEndian.AppendUint32(body, uint32(len(sids)))

	refID := uint32(0x00020004)
	for range sids {
		body = binary.LittleEndian.AppendUint32(body, refID)
		refID += 4
	}

	for _, sid := range sids {
		if len(sid) < 8 {
			return nil, fmt.Errorf("invalid sid (too short)")
		}
		subCount := uint32(sid[1])
		body = binary.LittleEndian.AppendUint32(body, subCount)
		body = append(body, sid...)
		if pad := Pad4(len(sid)); pad > 0 {
			body = append(body, make([]byte, pad)...)
		}
	}

	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)

	body = binary.LittleEndian.AppendUint16(body, 1)
	body = append(body, 0, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 2)

	return wrapRequest(callID, OpnumLsarLookupSids2, body), nil
}

// ParseLookupSids2Response is permissive — production needs careful NDR
// handling. Returns nil names rather than failing on structural surprise.
func ParseLookupSids2Response(body []byte) (names []TranslatedName, status uint32, err error) {
	r := newReader(body)
	domPtr := r.U32()
	if domPtr != 0 {
		r.SkipDomains()
	}
	nameCount := r.U32()
	namesPtr := r.U32()
	if namesPtr == 0 {
		return nil, r.Tail32(), nil
	}
	r.U32()

	type fixed struct {
		sidType uint16
		length  uint16
		maxLen  uint16
		namePtr uint32
		domIdx  int32
		flags   uint32
	}
	fixeds := make([]fixed, nameCount)
	for i := range fixeds {
		f := &fixeds[i]
		f.sidType = r.U16()
		r.U16()
		f.length = r.U16()
		f.maxLen = r.U16()
		f.namePtr = r.U32()
		f.domIdx = int32(r.U32())
		f.flags = r.U32()
	}
	out := make([]TranslatedName, nameCount)
	for i, f := range fixeds {
		if f.namePtr == 0 || f.length == 0 {
			out[i] = TranslatedName{SidType: f.sidType, DomainIdx: f.domIdx}
			continue
		}
		r.U32()
		r.U32()
		actual := r.U32()
		nameBytes := r.Bytes(int(actual) * 2)
		r.AlignTo(4)
		name := DecodeUTF16LE(nameBytes)
		out[i] = TranslatedName{SidType: f.sidType, Name: name, DomainIdx: f.domIdx}
	}

	r.U32()
	status = r.Tail32()
	return out, status, nil
}

// DecodeUTF16LE decodes the inverse of EncodeUTF16LE.
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
