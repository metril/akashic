package lsarpc

import "github.com/akashic-project/akashic/scanner/internal/dcerpc"

// skipDomains consumes a referenced_domains payload completely so the
// next reads in the LSARPC response land on translated_names.
//
// This handles the deferred name strings and SID payloads that follow
// the conformant trust-information array (per MS-LSAT §2.2.12 +
// MS-DTYP §2.2.7). It's specific to LSARPC's response shape, hence
// per-package rather than in dcerpc.
func skipDomains(r *dcerpc.Reader) {
	entries := r.U32()
	r.U32() // domains array referent ptr (we already entered the deferred buffer)
	r.U32() // max count of conformant array

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
	// MaxEntries at end of LSAPR_REFERENCED_DOMAIN_LIST struct.
	r.U32()
}
