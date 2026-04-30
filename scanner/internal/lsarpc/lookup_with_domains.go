package lsarpc

import (
	"errors"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// LookupResult is the per-SID translation including the domain name —
// what most callers really want for display ("DOMAIN\jdoe", not just
// "jdoe"). The existing TranslatedName / Lookup pair is preserved for
// the scan-time path that only stores the bare name.
type LookupResult struct {
	SidType uint16 // see SidTypeName for human-readable form
	Name    string // relative name (e.g., "jdoe"); empty if unresolved
	Domain  string // domain or local-machine name (e.g., "EXAMPLE")
}

// SID type values from MS-LSAT §2.2.12 (SID_NAME_USE).
const (
	SidTypeUser            uint16 = 1
	SidTypeGroup           uint16 = 2
	SidTypeDomain          uint16 = 3
	SidTypeAlias           uint16 = 4
	SidTypeWellKnownGroup  uint16 = 5
	SidTypeDeletedAccount  uint16 = 6
	SidTypeInvalid         uint16 = 7
	SidTypeUnknown         uint16 = 8
	SidTypeComputer        uint16 = 9
	SidTypeLabel           uint16 = 10
)

// SidTypeName maps the wire-format SID_NAME_USE to the lowercase strings
// the api/web layer expects in the `kind` column of principals_cache.
func SidTypeName(t uint16) string {
	switch t {
	case SidTypeUser:
		return "user"
	case SidTypeGroup:
		return "group"
	case SidTypeDomain:
		return "domain"
	case SidTypeAlias:
		return "alias"
	case SidTypeWellKnownGroup:
		return "well_known_group"
	case SidTypeDeletedAccount:
		return "deleted_account"
	case SidTypeInvalid:
		return "invalid"
	case SidTypeUnknown:
		return "unknown"
	case SidTypeComputer:
		return "computer"
	case SidTypeLabel:
		return "label"
	default:
		return ""
	}
}

// LookupWithDomains is the same wire call as Lookup() but returns the
// referenced-domain table so the caller can pair each translated name
// with its domain. For an unresolved SID the result row is zero-value
// — callers should treat empty Name as "unresolved".
func (c *Client) LookupWithDomains(sids [][]byte) ([]LookupResult, error) {
	if !c.opened {
		return nil, errors.New("lsarpc: policy not open")
	}
	pkt, err := BuildLookupSids2Request(c.nextCall(), c.handle, sids)
	if err != nil {
		return nil, err
	}
	if _, err := c.t.Write(pkt); err != nil {
		return nil, err
	}
	body, err := c.readResponseBody()
	if err != nil {
		return nil, err
	}
	out, _, err := parseLookupSids2WithDomains(body)
	return out, err
}

// parseLookupSids2WithDomains is a near-clone of ParseLookupSids2Response
// that ALSO extracts the referenced-domain names so each TranslatedName
// can be paired with its domain. The two parsers exist side-by-side
// rather than one calling the other because the wire format threads
// domain payloads BEFORE the names — so we have to read domain names
// up front, then names, then attach them in a second pass.
func parseLookupSids2WithDomains(body []byte) ([]LookupResult, uint32, error) {
	r := dcerpc.NewReader(body)
	domains := readDomainsTable(r)

	nameCount := r.U32()
	namesPtr := r.U32()
	if namesPtr == 0 {
		return nil, r.Tail32(), nil
	}
	r.U32() // max count of conformant translated_names array

	type fixed struct {
		sidType uint16
		length  uint16
		maxLen  uint16
		namePtr uint32
		domIdx  int32
		flags   uint32
	}
	fixeds := make([]fixed, nameCount)
	for i := range fixeds {
		f := &fixeds[i]
		f.sidType = r.U16()
		r.U16() // pad
		f.length = r.U16()
		f.maxLen = r.U16()
		f.namePtr = r.U32()
		f.domIdx = int32(r.U32())
		f.flags = r.U32()
	}
	out := make([]LookupResult, nameCount)
	for i, f := range fixeds {
		dom := ""
		if f.domIdx >= 0 && int(f.domIdx) < len(domains) {
			dom = domains[f.domIdx]
		}
		if f.namePtr == 0 || f.length == 0 {
			out[i] = LookupResult{SidType: f.sidType, Domain: dom}
			continue
		}
		r.U32() // max
		r.U32() // offset
		actual := r.U32()
		nameBytes := r.Bytes(int(actual) * 2)
		r.AlignTo(4)
		out[i] = LookupResult{
			SidType: f.sidType,
			Name:    dcerpc.DecodeUTF16LE(nameBytes),
			Domain:  dom,
		}
	}

	r.U32() // count_returned (we already used name_count)
	status := r.Tail32()
	return out, status, nil
}

// readDomainsTable parses an LSAPR_REFERENCED_DOMAIN_LIST and returns
// the domain Name strings. The wire shape (per MS-DTYP §2.2.7 plus the
// NDR encoding rules of MS-RPCE §2.2.4):
//
//	OUTER REF (caller already consumed)        u32   pointer ref-id
//	Entries                                    u32   count of domains
//	Domains pointer ref-id                     u32   non-zero => non-NULL
//	MaxEntries                                 u32   struct's third field
//	[deferred buffer for the Domains pointer]
//	    conformance count                      u32   max_is value
//	    array of LSAPR_TRUST_INFORMATION       12 bytes per entry:
//	        Length              u16
//	        MaximumLength       u16
//	        Buffer ref-id       u32
//	        Sid    ref-id       u32
//	    [per-entry deferred buffers]
//	        for each entry with non-NULL Buffer:
//	            max_count u32, offset u32, actual_count u32, chars[*2], pad to 4
//	        for each entry with non-NULL Sid:
//	            sub_authority_count u32, sid bytes, pad to 4
//
// Two NDR fields were missing in the original implementation: the
// deferred-array conformance count (between MaxEntries and the entry
// headers), AND the parser also incorrectly treated MaxEntries as a
// trailing footer. Both errors compounded into 4-byte-shifted reads
// that returned empty names for every entry — the bug masked by the
// prior LookupSids2 request itself faulting before the response got
// this far.
func readDomainsTable(r *dcerpc.Reader) []string {
	domPtr := r.U32()
	if domPtr == 0 {
		return nil
	}
	entries := r.U32()
	r.U32() // Domains pointer ref-id
	r.U32() // MaxEntries — last field of the LSAPR_REFERENCED_DOMAIN_LIST struct

	// NDR conformance count for the deferred Domains array. Always
	// equals Entries in well-formed responses; we don't validate.
	r.U32()

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
	out := make([]string, entries)
	for i, h := range hdrs {
		if h.namePtr != 0 {
			r.U32() // max
			r.U32() // offset
			actual := r.U32()
			nameBytes := r.Bytes(int(actual) * 2)
			r.AlignTo(4)
			out[i] = dcerpc.DecodeUTF16LE(nameBytes)
		}
		if h.sidPtr != 0 {
			subCount := r.U32()
			_ = r.Bytes(8 + int(subCount)*4)
			r.AlignTo(4)
		}
	}
	// No trailing MaxEntries here — that field was already read as the
	// third top-level u32 of LSAPR_REFERENCED_DOMAIN_LIST.
	return out
}
