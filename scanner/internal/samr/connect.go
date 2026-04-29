package samr

import (
	"encoding/binary"
	"unicode/utf16"
)

// BuildSamrConnect5Request encodes SamrConnect5 (opnum 64).
//
// Wire format (per [MS-SAMR] §3.1.5.1.4 + impacket reference):
//
//	[unique, string] PSAMPR_SERVER_NAME ServerName  // unique-ptr to WIDE_STRING
//	uint32 DesiredAccess
//	uint32 InVersion (= 1)
//	[switch_is(InVersion)] SAMPR_REVISION_INFO_V1 InRevisionInfo:
//	    uint32 Revision         (= 3 for Windows Server 2003+)
//	    uint32 SupportedFeatures (= 0)
//
// ServerName is a unique pointer to a NUL-terminated UTF-16LE string;
// when non-NULL it's followed by max_count, offset, actual_count, chars,
// pad-to-4. Most SAMR servers ignore the actual server name string but
// require a non-null pointer.
func BuildSamrConnect5Request(callID uint32, serverName string, desiredAccess uint32) []byte {
	body := make([]byte, 0, 64)

	// ServerName: unique pointer (referent 0x00020000) → WIDE_STRING.
	body = binary.LittleEndian.AppendUint32(body, 0x00020000)

	// WIDE_STRING is conformant + varying:
	//   uint32 max_count
	//   uint32 offset (0)
	//   uint32 actual_count
	//   WCHARs including trailing NUL
	//   pad to 4
	codes := utf16.Encode([]rune(serverName))
	codes = append(codes, 0) // NUL terminator
	count := uint32(len(codes))
	body = binary.LittleEndian.AppendUint32(body, count)
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, count)
	for _, c := range codes {
		body = binary.LittleEndian.AppendUint16(body, c)
	}
	body = AlignBytes(body, 4)

	// DesiredAccess
	body = binary.LittleEndian.AppendUint32(body, desiredAccess)

	// InVersion = 1
	body = binary.LittleEndian.AppendUint32(body, 1)

	// SAMPR_REVISION_INFO_V1 = (Revision=1, SupportedFeatures=0).
	// Revision=1 matches InVersion=1 — both name the V1 wire shape.
	// (Real servers ignore this value, but match what conformant clients do.)
	body = binary.LittleEndian.AppendUint32(body, 1)
	body = binary.LittleEndian.AppendUint32(body, 0)

	return wrapRequest(callID, OpnumSamrConnect5, body)
}

// ParseSamrConnect5Response parses the response body.
//
// Out shape:
//
//	uint32 OutVersion
//	[switch_is(OutVersion)] SAMPR_REVISION_INFO Out (8 bytes for V1)
//	[20]byte ServerHandle
//	uint32 ReturnCode (NTSTATUS)
func ParseSamrConnect5Response(body []byte) (Handle, uint32, error) {
	r := newReader(body)
	r.U32() // OutVersion
	r.U32() // Out.Revision
	r.U32() // Out.SupportedFeatures
	hbytes := r.Bytes(20)
	if hbytes == nil {
		return Handle{}, 0, ErrTruncated
	}
	var h Handle
	copy(h[:], hbytes)
	status := r.U32()
	return h, status, nil
}
