package lsarpc

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// skipDomains consumes a referenced_domains payload completely so the
// next reads in the LSARPC response land on translated_names.
//
// Wire shape: see readDomainsTable in lookup_with_domains.go for the
// full annotated layout. Two NDR fields were missing in the original
// version of this function (the deferred-array conformance count, and
// it incorrectly treated MaxEntries as a trailing footer rather than
// the struct's third top-level field). Both errors compounded to
// shift every subsequent translated_names read by 4 bytes — masked
// in production because the prior LsarLookupSids2 request itself
// faulted before responses got this far.
func skipDomains(r *dcerpc.Reader) {
	entries := r.U32()
	r.U32() // Domains pointer ref-id
	r.U32() // MaxEntries (3rd top-level field of LSAPR_REFERENCED_DOMAIN_LIST)
	r.U32() // NDR conformance count for the deferred Domains array

	type entryHdr struct {
		length, maxLen  uint16
		namePtr, sidPtr uint32
	}
	hdrs := make([]entryHdr, entries)
	for i := uint32(0); i < entries; i++ {
		hdrs[i].length = r.U16()
		hdrs[i].maxLen = r.U16()
		hdrs[i].namePtr = r.U32()
		hdrs[i].sidPtr = r.U32()
	}
	// Deferred payloads per entry.
	for _, h := range hdrs {
		if h.namePtr != 0 {
			// RPC_UNICODE_STRING deferred body: max_count(u32), offset(u32),
			// actual_count(u32), chars(actual*2), align(4).
			r.U32()
			r.U32()
			actual := r.U32()
			_ = r.Bytes(int(actual) * 2)
			r.AlignTo(4)
		}
		if h.sidPtr != 0 {
			// SID deferred body: max_count(u32), then SID bytes, then align(4).
			subCount := r.U32()
			_ = r.Bytes(8 + int(subCount)*4)
			r.AlignTo(4)
		}
	}
	// No trailing MaxEntries — already consumed as the third top-level u32.
}
