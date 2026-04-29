package samr

import (
	"encoding/binary"
	"fmt"
	"strconv"
	"strings"
)

// SID is the structural representation of a Windows security identifier.
//
// Authority is held in network (big-endian) byte order — that's the only
// big-endian field in a SID, per MS-DTYP §2.4.2.1.
//
// SubAuthority is a list of little-endian uint32 values; the final entry
// is conventionally the RID (relative identifier).
type SID struct {
	Revision     uint8
	Authority    [6]byte
	SubAuthority []uint32
}

// Handle is the 20-byte opaque RPC handle used by all SAMR procedures.
type Handle [20]byte

// ParseSidString parses an "S-1-5-21-…" string form into a SID.
func ParseSidString(s string) (SID, error) {
	if !strings.HasPrefix(s, "S-") && !strings.HasPrefix(s, "s-") {
		return SID{}, fmt.Errorf("%w: missing S- prefix", ErrInvalidSID)
	}
	parts := strings.Split(s[2:], "-")
	if len(parts) < 2 {
		return SID{}, fmt.Errorf("%w: too few components", ErrInvalidSID)
	}
	rev, err := strconv.ParseUint(parts[0], 10, 8)
	if err != nil {
		return SID{}, fmt.Errorf("%w: bad revision: %v", ErrInvalidSID, err)
	}
	authVal, err := strconv.ParseUint(parts[1], 10, 64)
	if err != nil {
		return SID{}, fmt.Errorf("%w: bad authority: %v", ErrInvalidSID, err)
	}
	if authVal > 0xFFFFFFFFFFFF {
		return SID{}, fmt.Errorf("%w: authority too large", ErrInvalidSID)
	}
	var auth [6]byte
	// Big-endian 48-bit integer (per MS-DTYP §2.4.2.1).
	auth[0] = byte(authVal >> 40)
	auth[1] = byte(authVal >> 32)
	auth[2] = byte(authVal >> 24)
	auth[3] = byte(authVal >> 16)
	auth[4] = byte(authVal >> 8)
	auth[5] = byte(authVal)

	subs := make([]uint32, 0, len(parts)-2)
	for _, p := range parts[2:] {
		v, err := strconv.ParseUint(p, 10, 32)
		if err != nil {
			return SID{}, fmt.Errorf("%w: bad sub-authority %q: %v", ErrInvalidSID, p, err)
		}
		subs = append(subs, uint32(v))
	}
	return SID{Revision: uint8(rev), Authority: auth, SubAuthority: subs}, nil
}

// String renders a SID in canonical "S-R-A-…" form.
func (s SID) String() string {
	authVal := uint64(s.Authority[0])<<40 |
		uint64(s.Authority[1])<<32 |
		uint64(s.Authority[2])<<24 |
		uint64(s.Authority[3])<<16 |
		uint64(s.Authority[4])<<8 |
		uint64(s.Authority[5])
	parts := []string{"S", strconv.FormatUint(uint64(s.Revision), 10), strconv.FormatUint(authVal, 10)}
	for _, sa := range s.SubAuthority {
		parts = append(parts, strconv.FormatUint(uint64(sa), 10))
	}
	return strings.Join(parts, "-")
}

// SplitDomainAndRid returns (domain SID without final RID, RID). Errors if
// the SID has no sub-authorities (no RID to split off).
func SplitDomainAndRid(s SID) (SID, uint32, error) {
	if len(s.SubAuthority) == 0 {
		return SID{}, 0, fmt.Errorf("%w: cannot split SID with no sub-authority", ErrInvalidSID)
	}
	rid := s.SubAuthority[len(s.SubAuthority)-1]
	domSubs := make([]uint32, len(s.SubAuthority)-1)
	copy(domSubs, s.SubAuthority[:len(s.SubAuthority)-1])
	return SID{
		Revision:     s.Revision,
		Authority:    s.Authority,
		SubAuthority: domSubs,
	}, rid, nil
}

// rawBytes (unexported) returns the on-wire SID byte sequence without the
// preceding NDR conformance count. Used internally where SIDs are embedded
// in non-conformant contexts.
func (s SID) rawBytes() []byte {
	out := make([]byte, 0, 8+len(s.SubAuthority)*4)
	out = append(out, s.Revision)
	out = append(out, byte(len(s.SubAuthority)))
	out = append(out, s.Authority[:]...)
	for _, sa := range s.SubAuthority {
		out = binary.LittleEndian.AppendUint32(out, sa)
	}
	return out
}
