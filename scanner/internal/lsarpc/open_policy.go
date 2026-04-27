package lsarpc

import (
	"encoding/binary"
	"fmt"
)

const (
	OpnumLsarOpenPolicy2 uint16 = 44
	OpnumLsarLookupSids2 uint16 = 57
	OpnumLsarClose       uint16 = 0
)

// PolicyHandle is the 20-byte opaque handle returned by LsarOpenPolicy2.
type PolicyHandle [20]byte

// BuildOpenPolicy2Request encodes an LsarOpenPolicy2 request with NULL system name.
func BuildOpenPolicy2Request(callID uint32, accessMask uint32) []byte {
	body := make([]byte, 0, 32)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 24)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, accessMask)

	return wrapRequest(callID, OpnumLsarOpenPolicy2, body)
}

func wrapRequest(callID uint32, opnum uint16, body []byte) []byte {
	header := PDUHeader{
		PType:   PtypeRequest,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: uint16(16 + 8 + len(body)),
		AuthLen: 0,
		CallID:  callID,
	}.Marshal()
	reqHeader := make([]byte, 8)
	binary.LittleEndian.PutUint32(reqHeader[0:4], uint32(len(body)))
	binary.LittleEndian.PutUint16(reqHeader[4:6], 0)
	binary.LittleEndian.PutUint16(reqHeader[6:8], opnum)
	out := append(header, reqHeader...)
	return append(out, body...)
}

// ParseOpenPolicy2Response decodes the response body.
func ParseOpenPolicy2Response(body []byte) (PolicyHandle, uint32, error) {
	var h PolicyHandle
	if len(body) < 24 {
		return h, 0, fmt.Errorf("open_policy2 response truncated: %d bytes", len(body))
	}
	copy(h[:], body[0:20])
	status := binary.LittleEndian.Uint32(body[20:24])
	return h, status, nil
}
