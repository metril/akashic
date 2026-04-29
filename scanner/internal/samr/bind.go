package samr

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// SAMR interface UUID 12345778-1234-ABCD-EF00-0123456789AC, version 1.0
// (per MS-SAMR §1.9). The literal byte sequence reverses the first three
// GUID groups (Data1, Data2, Data3) to little-endian and keeps Data4 in
// network order.
var samrUUID = [16]byte{
	0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
	0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xac,
}

const (
	samrVersion      uint16 = 1
	samrVersionMinor uint16 = 0
)

// BuildBindRequest constructs a DCE/RPC bind PDU for SAMR over NDR.
func BuildBindRequest(callID uint32, maxXmitFrag, maxRecvFrag uint16) []byte {
	return dcerpc.BuildBindRequest(callID, samrUUID, samrVersion, samrVersionMinor, maxXmitFrag, maxRecvFrag)
}
