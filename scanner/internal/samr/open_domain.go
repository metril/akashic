package samr

import "encoding/binary"

// BuildSamrOpenDomainRequest encodes SamrOpenDomain (opnum 7).
//
// Wire format:
//
//	[20]byte ServerHandle
//	uint32   DesiredAccess
//	RPC_SID  DomainId   (with leading conformance count)
func BuildSamrOpenDomainRequest(callID uint32, server Handle, desiredAccess uint32, domain SID) []byte {
	body := make([]byte, 0, 32+8+len(domain.SubAuthority)*4)
	body = append(body, server[:]...)
	body = binary.LittleEndian.AppendUint32(body, desiredAccess)
	body = append(body, EncodeRPCSID(domain)...)
	return wrapRequest(callID, OpnumSamrOpenDomain, body)
}

// ParseSamrOpenDomainResponse parses the 20-byte handle + NTSTATUS.
func ParseSamrOpenDomainResponse(body []byte) (Handle, uint32, error) {
	if len(body) < 24 {
		return Handle{}, 0, ErrTruncated
	}
	var h Handle
	copy(h[:], body[:20])
	status := binary.LittleEndian.Uint32(body[20:24])
	return h, status, nil
}
