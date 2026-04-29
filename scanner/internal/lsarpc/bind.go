package lsarpc

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// LSARPC interface UUID 12345778-1234-ABCD-EF00-0123456789AB v0.0
// (per MS-LSAT §1.9).
var lsarpcUUID = [16]byte{
	0x78, 0x57, 0x34, 0x12, 0x34, 0x12, 0xcd, 0xab,
	0xef, 0x00, 0x01, 0x23, 0x45, 0x67, 0x89, 0xab,
}

const (
	lsarpcVersion      uint16 = 0
	lsarpcVersionMinor uint16 = 0
)

// BuildBindRequest constructs a DCE/RPC bind PDU for LSARPC over NDR.
func BuildBindRequest(callID uint32, maxXmitFrag, maxRecvFrag uint16) []byte {
	return dcerpc.BuildBindRequest(callID, lsarpcUUID, lsarpcVersion, lsarpcVersionMinor, maxXmitFrag, maxRecvFrag)
}
