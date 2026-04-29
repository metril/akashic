package dcerpc

import "encoding/binary"

// NDR transfer syntax UUID (8a885d04-1ceb-11c9-9fe8-08002b104860) v2 —
// the only transfer syntax we use for either LSARPC or SAMR bindings.
var ndrTransferUUID = [16]byte{
	0x04, 0x5d, 0x88, 0x8a, 0xeb, 0x1c, 0xc9, 0x11,
	0x9f, 0xe8, 0x08, 0x00, 0x2b, 0x10, 0x48, 0x60,
}

const ndrTransferVersion uint32 = 2

// BuildBindRequest constructs a DCE/RPC bind PDU for the given abstract
// interface UUID over NDR. Per-binding packages call this with their
// interface UUID + version (e.g. SAMR is 12345778-1234-ABCD-EF00-0123456789AC v1.0,
// LSARPC is 12345778-1234-ABCD-EF00-0123456789AB v0.0).
func BuildBindRequest(callID uint32, ifaceUUID [16]byte, ifaceVersion, ifaceVersionMinor uint16, maxXmitFrag, maxRecvFrag uint16) []byte {
	body := make([]byte, 0, 56)
	body = binary.LittleEndian.AppendUint16(body, maxXmitFrag)
	body = binary.LittleEndian.AppendUint16(body, maxRecvFrag)
	body = binary.LittleEndian.AppendUint32(body, 0) // assoc_group_id
	body = append(body, 1, 0, 0, 0)                  // p_context_elem (1) + reserved
	body = binary.LittleEndian.AppendUint16(body, 0) // p_cont_id
	body = append(body, 1, 0)                        // n_transfer_syn (1) + reserved
	body = append(body, ifaceUUID[:]...)
	body = binary.LittleEndian.AppendUint16(body, ifaceVersion)
	body = binary.LittleEndian.AppendUint16(body, ifaceVersionMinor)
	body = append(body, ndrTransferUUID[:]...)
	body = binary.LittleEndian.AppendUint32(body, ndrTransferVersion)

	pdu := PDUHeader{
		PType:   PtypeBind,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: uint16(16 + len(body)),
		AuthLen: 0,
		CallID:  callID,
	}.Marshal()
	return append(pdu, body...)
}

// WrapRequest wraps a request body in a DCE/RPC Request PDU with the
// given opnum. Callers supply the (already serialized) NDR body.
//
// Request PDU body header (MS-RPCE §2.2.2.4):
//
//	uint32 alloc_hint
//	uint16 p_cont_id
//	uint16 opnum
//	...stub data
func WrapRequest(callID uint32, opnum uint16, body []byte) []byte {
	hdr := make([]byte, 8)
	binary.LittleEndian.PutUint32(hdr[0:4], uint32(len(body)))
	binary.LittleEndian.PutUint16(hdr[4:6], 0)
	binary.LittleEndian.PutUint16(hdr[6:8], opnum)

	pdu := PDUHeader{
		PType:   PtypeRequest,
		Flags:   PfcFirstFrag | PfcLastFrag,
		FragLen: uint16(16 + len(hdr) + len(body)),
		AuthLen: 0,
		CallID:  callID,
	}.Marshal()
	out := make([]byte, 0, len(pdu)+len(hdr)+len(body))
	out = append(out, pdu...)
	out = append(out, hdr...)
	out = append(out, body...)
	return out
}
