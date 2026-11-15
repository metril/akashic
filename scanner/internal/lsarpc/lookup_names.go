package lsarpc

import (
	"encoding/binary"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/dcerpc"
)

// NTSTATUS values LsarLookupNames3 can return for partial/no mapping.
const (
	StatusSomeNotMapped uint32 = 0x00000107
	StatusNoneMapped    uint32 = 0xC0000073
)

// ErrNameNotFound is returned by Client.LookupNames when LSA maps none of
// the supplied names (STATUS_NONE_MAPPED).
var ErrNameNotFound = errors.New("lsarpc: name not found")

// NameResult is the translation of one input name to a SID.
type NameResult struct {
	Mapped    bool
	SidType   uint16
	Sid       []byte // raw SID bytes (nil when unmapped)
	SidString string // "S-1-5-21-…" (empty when unmapped)
	Domain    string // referenced domain name
	DomainSid string // referenced domain SID string, if returned
}

// BuildLookupNames3Request encodes the input side of LsarLookupNames3
// (MS-LSAT §3.1.4.7, opnum 68).
func BuildLookupNames3Request(callID uint32, h PolicyHandle, names []string) ([]byte, error) {
	if len(names) == 0 {
		return nil, errors.New("no names")
	}
	body := make([]byte, 0, 64+len(names)*32)
	body = append(body, h[:]...)

	body = binary.LittleEndian.AppendUint32(body, uint32(len(names)))
	// Names: conformant array of RPC_UNICODE_STRING.
	body = binary.LittleEndian.AppendUint32(body, uint32(len(names)))
	encoded := make([][]byte, len(names))
	refID := uint32(0x00020000)
	for i, n := range names {
		if n == "" {
			return nil, errors.New("empty name")
		}
		encoded[i] = dcerpc.EncodeUTF16LE(n)
		l := uint16(len(encoded[i]))
		body = binary.LittleEndian.AppendUint16(body, l)
		body = binary.LittleEndian.AppendUint16(body, l)
		body = binary.LittleEndian.AppendUint32(body, refID)
		refID += 4
	}
	for _, e := range encoded {
		chars := uint32(len(e) / 2)
		body = binary.LittleEndian.AppendUint32(body, chars)
		body = binary.LittleEndian.AppendUint32(body, 0)
		body = binary.LittleEndian.AppendUint32(body, chars)
		body = append(body, e...)
		if pad := dcerpc.Pad4(len(e)); pad > 0 {
			body = append(body, make([]byte, pad)...)
		}
	}

	// TranslatedSids (in/out, empty): Entries=0, Sids=NULL.
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 0)
	// LookupLevel = LsapLookupWksta (1), 2-byte enum + pad.
	body = binary.LittleEndian.AppendUint16(body, 1)
	body = append(body, 0, 0)
	// MappedCount (in/out) = 0.
	body = binary.LittleEndian.AppendUint32(body, 0)
	// LookupOptions = 0, ClientRevision = 2.
	body = binary.LittleEndian.AppendUint32(body, 0)
	body = binary.LittleEndian.AppendUint32(body, 2)

	return dcerpc.WrapRequest(callID, OpnumLsarLookupNames3, body), nil
}

// ParseLookupNames3Response decodes the response body into one NameResult
// per input name plus the trailing NTSTATUS. A name is Mapped when LSA
// returned a SID for it.
func ParseLookupNames3Response(body []byte) ([]NameResult, uint32, error) {
	if len(body) < 4 {
		return nil, 0, fmt.Errorf("lookup_names3 response truncated: %d bytes", len(body))
	}
	r := dcerpc.NewReader(body)
	domains, err := readDomainsWithSids(r)
	if err != nil {
		return nil, 0, err
	}

	entries := r.U32()
	sidsPtr := r.U32()
	if sidsPtr == 0 || entries == 0 {
		return nil, r.Tail32(), nil
	}
	if int(entries) > len(body)/16 {
		return nil, 0, errors.New("lookup_names3: implausible entry count")
	}
	r.U32() // conformance count

	type fixed struct {
		sidType uint16
		sidPtr  uint32
		domIdx  int32
	}
	fx := make([]fixed, entries)
	for i := range fx {
		fx[i].sidType = r.U16()
		r.U16() // pad
		fx[i].sidPtr = r.U32()
		fx[i].domIdx = int32(r.U32())
		r.U32() // flags
	}
	out := make([]NameResult, entries)
	for i, f := range fx {
		res := NameResult{SidType: f.sidType}
		if f.domIdx >= 0 && int(f.domIdx) < len(domains) {
			res.Domain = domains[f.domIdx].name
			res.DomainSid = domains[f.domIdx].sid
		}
		if f.sidPtr != 0 {
			sub := r.U32()
			if sub > 15 {
				return nil, 0, errors.New("lookup_names3: bad sid sub-authority count")
			}
			sid := r.Bytes(8 + int(sub)*4)
			if sid == nil {
				return nil, 0, errors.New("lookup_names3: truncated sid")
			}
			r.AlignTo(4)
			res.Sid = append([]byte(nil), sid...)
			res.SidString = SidBytesToString(res.Sid)
			res.Mapped = res.SidString != ""
		}
		out[i] = res
	}
	r.U32() // MappedCount
	return out, r.Tail32(), nil
}

type refDomain struct{ name, sid string }

// readDomainsWithSids is readDomainsTable plus the domain SIDs.
func readDomainsWithSids(r *dcerpc.Reader) ([]refDomain, error) {
	if r.U32() == 0 {
		return nil, nil
	}
	entries := r.U32()
	r.U32() // Domains ref-id
	r.U32() // MaxEntries
	r.U32() // conformance count
	if entries > 1024 {
		return nil, errors.New("lookup_names3: implausible domain count")
	}
	type hdr struct{ namePtr, sidPtr uint32 }
	hdrs := make([]hdr, entries)
	for i := range hdrs {
		r.U16()
		r.U16()
		hdrs[i].namePtr = r.U32()
		hdrs[i].sidPtr = r.U32()
	}
	out := make([]refDomain, entries)
	for i, h := range hdrs {
		if h.namePtr != 0 {
			r.U32()
			r.U32()
			actual := r.U32()
			b := r.Bytes(int(actual) * 2)
			r.AlignTo(4)
			out[i].name = dcerpc.DecodeUTF16LE(b)
		}
		if h.sidPtr != 0 {
			sub := r.U32()
			if sub > 15 {
				return nil, errors.New("lookup_names3: bad domain sid")
			}
			b := r.Bytes(8 + int(sub)*4)
			r.AlignTo(4)
			if b != nil {
				out[i].sid = SidBytesToString(b)
			}
		}
	}
	return out, nil
}

// SidBytesToString renders on-wire SID bytes as "S-1-…". Returns "" when
// malformed.
func SidBytesToString(b []byte) string {
	if len(b) < 8 {
		return ""
	}
	n := int(b[1])
	if len(b) < 8+n*4 {
		return ""
	}
	var auth uint64
	for _, v := range b[2:8] {
		auth = auth<<8 | uint64(v)
	}
	var sb strings.Builder
	sb.WriteString("S-" + strconv.Itoa(int(b[0])) + "-" + strconv.FormatUint(auth, 10))
	for i := 0; i < n; i++ {
		sb.WriteString("-" + strconv.FormatUint(uint64(binary.LittleEndian.Uint32(b[8+i*4:])), 10))
	}
	return sb.String()
}

// LookupNames resolves names (user, DOMAIN\user, user@domain) to SIDs via
// LsarLookupNames3. Returns ErrNameNotFound on STATUS_NONE_MAPPED; names
// LSA could not map under STATUS_SOME_NOT_MAPPED have Mapped=false.
func (c *Client) LookupNames(names []string) ([]NameResult, error) {
	if !c.opened {
		return nil, errors.New("lsarpc: policy not open")
	}
	pkt, err := BuildLookupNames3Request(c.nextCall(), c.handle, names)
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
	res, status, err := ParseLookupNames3Response(body)
	if err != nil {
		return nil, err
	}
	switch status {
	case 0, StatusSomeNotMapped:
		if len(res) != len(names) {
			return nil, fmt.Errorf("lookup_names3: got %d results for %d names", len(res), len(names))
		}
		return res, nil
	case StatusNoneMapped:
		return nil, ErrNameNotFound
	default:
		return nil, fmt.Errorf("LsarLookupNames3 ntstatus=0x%x", status)
	}
}
