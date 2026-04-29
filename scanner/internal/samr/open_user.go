package samr

import (
	"encoding/binary"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// BuildSamrOpenUserRequest encodes SamrOpenUser (opnum 34).
//
// Wire format:
//
//	[20]byte DomainHandle
//	uint32   DesiredAccess
//	uint32   UserId (RID)
func BuildSamrOpenUserRequest(callID uint32, domain Handle, desiredAccess uint32, rid uint32) []byte {
	body := make([]byte, 0, 28)
	body = append(body, domain[:]...)
	body = binary.LittleEndian.AppendUint32(body, desiredAccess)
	body = binary.LittleEndian.AppendUint32(body, rid)
	return dcerpc.WrapRequest(callID, OpnumSamrOpenUser, body)
}

// ParseSamrOpenUserResponse parses 20-byte handle + NTSTATUS.
func ParseSamrOpenUserResponse(body []byte) (Handle, uint32, error) {
	if len(body) < 24 {
		return Handle{}, 0, dcerpc.ErrTruncated
	}
	var h Handle
	copy(h[:], body[:20])
	status := binary.LittleEndian.Uint32(body[20:24])
	return h, status, nil
}
