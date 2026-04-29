// Package dcerpc holds protocol primitives shared by every DCE/RPC binding
// the scanner speaks (LSARPC, SAMR, …): PDU framing, byte reader, generic
// NDR helpers, the bind-request builder, and a small set of common error
// sentinels.
//
// Per-binding packages (e.g. internal/lsarpc, internal/samr) supply the
// interface UUID, opcode constants, and per-opcode request/response codecs.
package dcerpc

import "encoding/binary"

// DCE/RPC PDU types (MS-RPCE §2.2.2.1).
const (
	PtypeRequest  byte = 0
	PtypeResponse byte = 2
	PtypeFault    byte = 3
	PtypeBind     byte = 11
	PtypeBindAck  byte = 12
	PtypeBindNak  byte = 13
)

// PDU pfc_flags (MS-RPCE §2.2.2.3).
const (
	PfcFirstFrag     byte = 0x01
	PfcLastFrag      byte = 0x02
	PfcPendingCancel byte = 0x04
	PfcConcurrentMux byte = 0x10
	PfcMaybe         byte = 0x40
	PfcObjectUuid    byte = 0x80
)

// PDUHeader is the 16-byte common DCE/RPC PDU header (MS-RPCE §2.2.2.1).
type PDUHeader struct {
	PType   byte
	Flags   byte
	FragLen uint16
	AuthLen uint16
	CallID  uint32
}

func (h PDUHeader) Marshal() []byte {
	out := make([]byte, 16)
	out[0] = 5 // rpc_vers
	out[1] = 0 // rpc_vers_minor
	out[2] = h.PType
	out[3] = h.Flags
	out[4] = 0x10 // packed_drep[0]: little-endian, ASCII, IEEE
	binary.LittleEndian.PutUint16(out[8:10], h.FragLen)
	binary.LittleEndian.PutUint16(out[10:12], h.AuthLen)
	binary.LittleEndian.PutUint32(out[12:16], h.CallID)
	return out
}

func ParsePDUHeader(b []byte) (PDUHeader, error) {
	var h PDUHeader
	if len(b) < 16 {
		return h, ErrTruncated
	}
	h.PType = b[2]
	h.Flags = b[3]
	h.FragLen = binary.LittleEndian.Uint16(b[8:10])
	h.AuthLen = binary.LittleEndian.Uint16(b[10:12])
	h.CallID = binary.LittleEndian.Uint32(b[12:16])
	return h, nil
}
