package samr

import "encoding/binary"

// SAMR interface UUID 12345778-1234-ABCD-EF00-0123456789AC, version 1.0
// (per MS-SAMR §1.9).
//
// DCE/RPC binary GUIDs are little-endian per field, so the literal byte
// sequence reverses the first three groups (Data1, Data2, Data3) and
// keeps Data4 (the trailing 8 bytes) in network order.
var samrUUID = [16]byte{
	0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
	0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xac,
}

var (
	samrVersion      uint16 = 1
	samrVersionMinor uint16 = 0
)

var ndrTransferUUID = [16]byte{
	0x04, 0x5d, 0x88, 0x8a, 0xeb, 0x1c, 0xc9, 0x11,
	0x9f, 0xe8, 0x08, 0x00, 0x2b, 0x10, 0x48, 0x60,
}

var ndrTransferVersion uint32 = 2

// BuildBindRequest constructs a DCE/RPC bind PDU for SAMR over NDR.
func BuildBindRequest(callID uint32, maxXmitFrag, maxRecvFrag uint16) []byte {
	body := make([]byte, 0, 56)
	body = binary.LittleEndian.AppendUint16(body, maxXmitFrag)
	body = binary.LittleEndian.AppendUint16(body, maxRecvFrag)
	body = binary.LittleEndian.AppendUint32(body, 0)        // assoc_group_id
	body = append(body, 1, 0, 0, 0)                         // p_context_elem (1) + reserved
	body = binary.LittleEndian.AppendUint16(body, 0)        // p_cont_id
	body = append(body, 1, 0)                               // n_transfer_syn (1) + reserved
	body = append(body, samrUUID[:]...)
	body = binary.LittleEndian.AppendUint16(body, samrVersion)
	body = binary.LittleEndian.AppendUint16(body, samrVersionMinor)
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
