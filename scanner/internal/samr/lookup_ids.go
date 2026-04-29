package samr

import "encoding/binary"

// BuildSamrLookupIdsInDomainRequest encodes SamrLookupIdsInDomain (opnum 18).
//
// Wire format (per [MS-SAMR] §3.1.5.11.2):
//
//	[20]byte DomainHandle
//	uint32   Count
//	[in, size_is(1000), length_is(Count)] uint32 RelativeIds[]:
//	    uint32 max_count = 1000
//	    uint32 offset    = 0
//	    uint32 actual    = Count
//	    Count × uint32 RIDs
func BuildSamrLookupIdsInDomainRequest(callID uint32, domain Handle, rids []uint32) []byte {
	count := uint32(len(rids))
	body := make([]byte, 0, 24+12+len(rids)*4)
	body = append(body, domain[:]...)
	body = binary.LittleEndian.AppendUint32(body, count)
	// Conformant-varying header for the RIDs array.
	body = binary.LittleEndian.AppendUint32(body, 1000) // max_count
	body = binary.LittleEndian.AppendUint32(body, 0)    // offset
	body = binary.LittleEndian.AppendUint32(body, count) // actual_count
	for _, rid := range rids {
		body = binary.LittleEndian.AppendUint32(body, rid)
	}
	return wrapRequest(callID, OpnumSamrLookupIdsInDomain, body)
}

// ParseSamrLookupIdsInDomainResponse extracts the resolved names. We
// deliberately ignore the SidUse[] array since the resolution is for
// group RIDs returned by GetGroupsForUser — they're known to be groups.
//
// Wire format:
//
//	uint32 NamesPtr        (referent for PSAMPR_RETURNED_USTRING_ARRAY)
//	if NamesPtr != 0:
//	  uint32 NamesCount
//	  uint32 ElementPtr   (referent for the array of RPC_UNICODE_STRING)
//	  if ElementPtr != 0:
//	    uint32 max_count = NamesCount
//	    NamesCount × { uint16 length, uint16 maxLen, uint32 bufferPtr }
//	    For each entry whose bufferPtr != 0, a deferred buffer:
//	      uint32 max_count
//	      uint32 offset
//	      uint32 actual_count
//	      actual_count × uint16 (UTF-16LE chars)
//	      pad to 4
//	uint32 UsePtr ... (skip)
//	uint32 NTSTATUS (at tail)
func ParseSamrLookupIdsInDomainResponse(body []byte) ([]string, uint32, error) {
	r := newReader(body)
	namesPtr := r.U32()
	if namesPtr == 0 {
		return nil, r.Tail32(), nil
	}
	count := r.U32()
	elementPtr := r.U32()
	if elementPtr == 0 {
		return nil, r.Tail32(), nil
	}
	r.U32() // max_count = count

	type entryHdr struct {
		length, maxLen uint16
		bufPtr         uint32
	}
	hdrs := make([]entryHdr, count)
	for i := range hdrs {
		hdrs[i].length = r.U16()
		hdrs[i].maxLen = r.U16()
		hdrs[i].bufPtr = r.U32()
	}
	out := make([]string, count)
	for i, h := range hdrs {
		if h.bufPtr == 0 || h.length == 0 {
			out[i] = ""
			continue
		}
		r.U32() // max_count
		r.U32() // offset
		actual := r.U32()
		nameBytes := r.Bytes(int(actual) * 2)
		r.AlignTo(4)
		out[i] = DecodeUTF16LE(nameBytes)
	}

	status := r.Tail32()
	return out, status, nil
}
