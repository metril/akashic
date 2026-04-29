package samr

import "encoding/binary"

// SAMR opnums (per [MS-SAMR] §3.1.5.* indexed table).
const (
	OpnumSamrCloseHandle         uint16 = 1
	OpnumSamrOpenDomain          uint16 = 7
	OpnumSamrLookupIdsInDomain   uint16 = 18
	OpnumSamrOpenUser            uint16 = 34
	OpnumSamrGetGroupsForUser    uint16 = 39
	OpnumSamrConnect5            uint16 = 64
)

// SAMR access masks (per [MS-SAMR] §2.2.1.*).
const (
	// SamrConnect5
	SamServerConnect       uint32 = 0x00000001
	SamServerLookupDomain  uint32 = 0x00000020
	SamServerAllAccess     uint32 = 0x000F003F

	// SamrOpenDomain
	DomainLookup            uint32 = 0x00000200
	DomainListAccounts      uint32 = 0x00000004
	DomainReadGeneric       uint32 = 0x00020205

	// SamrOpenUser
	UserReadGeneric         uint32 = 0x0002031A
	UserReadGroupInformation uint32 = 0x00000100
)

// wrapRequest wraps a request body in a DCE/RPC Request PDU with the given
// opnum. Caller supplies the (already serialized) NDR body.
func wrapRequest(callID uint32, opnum uint16, body []byte) []byte {
	// Request PDU body:
	//   uint32 alloc_hint
	//   uint16 p_cont_id
	//   uint16 opnum
	//   ...stub data
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
