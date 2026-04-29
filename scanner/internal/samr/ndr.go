package samr

// SAMR-specific NDR encoding/decoding. Generic DCE/RPC NDR helpers
// (EncodeUTF16LE, DecodeUTF16LE, Pad4, AlignBytes, EncodeRPCUnicodeString)
// live in scanner/internal/dcerpc.

import (
	"encoding/binary"
	"fmt"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// EncodeRPCSID encodes an RPC_SID (MS-DTYP §2.4.2.3) preceded by its NDR
// conformance count, suitable for embedding in a request body.
//
// Wire format:
//
//	uint32 conformance_count = SubAuthCount
//	uint8  Revision (1)
//	uint8  SubAuthCount
//	[6]byte IdentifierAuthority   (big-endian per MS-DTYP §2.4.2.1)
//	[N]uint32 SubAuthority        (little-endian)
//
// Padded to 4-byte boundary at the end.
//
// Note: the IdentifierAuthority is six bytes interpreted as a 48-bit
// big-endian integer; this is the only big-endian field in a SID.
// Sub-authorities follow in little-endian.
func EncodeRPCSID(sid SID) []byte {
	subCount := len(sid.SubAuthority)
	out := make([]byte, 0, 12+subCount*4)
	out = binary.LittleEndian.AppendUint32(out, uint32(subCount))
	out = append(out, sid.Revision)
	out = append(out, byte(subCount))
	out = append(out, sid.Authority[:]...)
	for _, sa := range sid.SubAuthority {
		out = binary.LittleEndian.AppendUint32(out, sa)
	}
	return dcerpc.AlignBytes(out, 4)
}

// DecodeRPCSID parses bytes produced by EncodeRPCSID (or the wire form of
// RPC_SID with leading conformance count). Used by tests; production
// SAMR responses currently never embed RPC_SIDs we need to parse.
func DecodeRPCSID(b []byte) (SID, int, error) {
	if len(b) < 12 {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid too short")
	}
	conformance := binary.LittleEndian.Uint32(b[0:4])
	revision := b[4]
	subCount := b[5]
	if uint32(subCount) != conformance {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid conformance mismatch (%d vs %d)", conformance, subCount)
	}
	if len(b) < 8+int(subCount)*4+4 {
		return SID{}, 0, fmt.Errorf("samr: rpc_sid truncated")
	}
	var auth [6]byte
	copy(auth[:], b[6:12])
	subs := make([]uint32, subCount)
	for i := 0; i < int(subCount); i++ {
		subs[i] = binary.LittleEndian.Uint32(b[12+i*4 : 16+i*4])
	}
	consumed := 12 + int(subCount)*4
	consumed += dcerpc.Pad4(consumed)
	return SID{Revision: revision, Authority: auth, SubAuthority: subs}, consumed, nil
}
