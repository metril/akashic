package lsarpc

import (
	"encoding/binary"
	"fmt"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
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
		if pad := dcerpc.Pad4(len(sid)); pad > 0 {
			body = append(body, make([]byte, pad)...)
		}
	}

	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)

	body = binary.LittleEndian.AppendUint16(body, 1)
	body = append(body, 0, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	// LookupOptions — MS-LSAT §3.1.4.11 — 0 = LSAP_LOOKUP_OPTION_ALL
	// (translate everything LSA can find, including well-known and
	// foreign-domain SIDs). Omitting this u32 was the source of the
	// "RPC_X_INVALID_TAG" fault every domain-SID lookup returned: the
	// server read our ClientRevision as LookupOptions, then ran out of
	// bytes for ClientRevision and bailed before LSA logic even ran.
	// Well-known SIDs masked the bug because the resolver short-
	// circuits them in the WellKnownSIDName table before any RPC.
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 2)

	return dcerpc.WrapRequest(callID, OpnumLsarLookupSids2, body), nil
}

// ParseLookupSids2Response is permissive — production needs careful NDR
// handling. Returns nil names rather than failing on structural surprise.
func ParseLookupSids2Response(body []byte) (names []TranslatedName, status uint32, err error) {
	r := dcerpc.NewReader(body)
	domPtr := r.U32()
	if domPtr != 0 {
		skipDomains(r)
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
		name := dcerpc.DecodeUTF16LE(nameBytes)
		out[i] = TranslatedName{SidType: f.sidType, Name: name, DomainIdx: f.domIdx}
	}

	r.U32()
	status = r.Tail32()
	return out, status, nil
}
