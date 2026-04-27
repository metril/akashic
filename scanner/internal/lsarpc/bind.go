package lsarpc

import "encoding/binary"

// LSARPC interface (MS-LSAT §1.9).
var lsarpcUUID = [16]byte{
	0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
	0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab,
}
var lsarpcVersion uint16 = 0
var lsarpcVersionMinor uint16 = 0

var ndrTransferUUID = [16]byte{
	0x04, 0x5d, 0x88, 0x8a, 0xeb, 0x1c, 0xc9, 0x11,
	0x9f, 0xe8, 0x08, 0x00, 0x2b, 0x10, 0x48, 0x60,
}
var ndrTransferVersion uint32 = 2

// BuildBindRequest constructs a DCE/RPC bind PDU for LSARPC over NDR.
func BuildBindRequest(callID uint32, maxXmitFrag, maxRecvFrag uint16) []byte {
	body := make([]byte, 0, 56)
	body = binary.LittleEndian.AppendUint16(body, maxXmitFrag)
	body = binary.LittleEndian.AppendUint16(body, maxRecvFrag)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = append(body, 1, 0, 0, 0)
	body = binary.LittleEndian.AppendUint16(body, 0)
	body = append(body, 1, 0)
	body = append(body, lsarpcUUID[:]...)
	body = binary.LittleEndian.AppendUint16(body, lsarpcVersion)
	body = binary.LittleEndian.AppendUint16(body, lsarpcVersionMinor)
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
