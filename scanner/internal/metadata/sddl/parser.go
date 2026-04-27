package sddl

import (
	"encoding/binary"
	"fmt"
)

type ParsedACE struct {
	AceType string
	Flags   []string
	Mask    []string
	SID     string
}

type ParsedSecurityDescriptor struct {
	Control     []string
	OwnerSID    string
	GroupSID    string
	DaclEntries []ParsedACE
}

// ParseSecurityDescriptor parses a self-relative NT security descriptor per MS-DTYP §2.4.6.
func ParseSecurityDescriptor(b []byte) (*ParsedSecurityDescriptor, error) {
	if len(b) < 20 {
		return nil, fmt.Errorf("sd: too short (%d bytes)", len(b))
	}
	if b[0] != 1 {
		return nil, fmt.Errorf("sd: unsupported revision %d", b[0])
	}
	control := binary.LittleEndian.Uint16(b[2:4])
	ownerOff := binary.LittleEndian.Uint32(b[4:8])
	groupOff := binary.LittleEndian.Uint32(b[8:12])
	daclOff := binary.LittleEndian.Uint32(b[16:20])

	out := &ParsedSecurityDescriptor{
		Control: parseControlFlags(control),
	}
	if ownerOff != 0 {
		sid, _, err := ParseSID(b[ownerOff:])
		if err != nil {
			return nil, fmt.Errorf("owner: %w", err)
		}
		out.OwnerSID = sid
	}
	if groupOff != 0 {
		sid, _, err := ParseSID(b[groupOff:])
		if err != nil {
			return nil, fmt.Errorf("group: %w", err)
		}
		out.GroupSID = sid
	}
	if daclOff != 0 {
		entries, err := parseACL(b[daclOff:])
		if err != nil {
			return nil, fmt.Errorf("dacl: %w", err)
		}
		out.DaclEntries = entries
	}
	return out, nil
}

var sdControlFlags = []struct {
	bit  uint16
	name string
}{
	{0x0001, "owner_defaulted"},
	{0x0002, "group_defaulted"},
	{0x0004, "dacl_present"},
	{0x0008, "dacl_defaulted"},
	{0x0010, "sacl_present"},
	{0x0020, "sacl_defaulted"},
	{0x0100, "dacl_auto_inherit_req"},
	{0x0200, "sacl_auto_inherit_req"},
	{0x0400, "dacl_auto_inherited"},
	{0x0800, "sacl_auto_inherited"},
	{0x1000, "dacl_protected"},
	{0x2000, "sacl_protected"},
	{0x4000, "rm_control_valid"},
	{0x8000, "self_relative"},
}

func parseControlFlags(c uint16) []string {
	var out []string
	for _, f := range sdControlFlags {
		if c&f.bit != 0 {
			out = append(out, f.name)
		}
	}
	return out
}

func parseACL(b []byte) ([]ParsedACE, error) {
	if len(b) < 8 {
		return nil, fmt.Errorf("acl: too short")
	}
	count := binary.LittleEndian.Uint16(b[4:6])
	body := b[8:]
	var entries []ParsedACE
	for i := 0; i < int(count); i++ {
		if len(body) < 4 {
			return nil, fmt.Errorf("ace[%d]: header truncated", i)
		}
		aceType := body[0]
		flags := body[1]
		size := binary.LittleEndian.Uint16(body[2:4])
		if int(size) > len(body) {
			return nil, fmt.Errorf("ace[%d]: size %d exceeds remaining %d", i, size, len(body))
		}
		ace, err := parseACE(aceType, flags, body[4:size])
		if err != nil {
			return nil, fmt.Errorf("ace[%d]: %w", i, err)
		}
		entries = append(entries, ace)
		body = body[size:]
	}
	return entries, nil
}

func parseACE(aceType, flags byte, body []byte) (ParsedACE, error) {
	if len(body) < 4 {
		return ParsedACE{}, fmt.Errorf("ace body too short")
	}
	mask := binary.LittleEndian.Uint32(body[0:4])
	sid, _, err := ParseSID(body[4:])
	if err != nil {
		return ParsedACE{}, err
	}
	return ParsedACE{
		AceType: aceTypeName(aceType),
		Flags:   parseAceFlags(flags),
		Mask:    parseAccessMask(mask),
		SID:     sid,
	}, nil
}

func aceTypeName(t byte) string {
	switch t {
	case 0x00:
		return "allow"
	case 0x01:
		return "deny"
	case 0x02:
		return "audit"
	default:
		return "unknown"
	}
}

var aceFlagBits = []struct {
	bit  byte
	name string
}{
	{0x01, "object_inherit"},
	{0x02, "container_inherit"},
	{0x04, "no_propagate"},
	{0x08, "inherit_only"},
	{0x10, "inherited"},
	{0x40, "successful_access"},
	{0x80, "failed_access"},
}

func parseAceFlags(f byte) []string {
	var out []string
	for _, b := range aceFlagBits {
		if f&b.bit != 0 {
			out = append(out, b.name)
		}
	}
	return out
}

var accessMaskBits = []struct {
	bit  uint32
	name string
}{
	{0x00000001, "READ_DATA"},
	{0x00000002, "WRITE_DATA"},
	{0x00000004, "APPEND_DATA"},
	{0x00000008, "READ_EA"},
	{0x00000010, "WRITE_EA"},
	{0x00000020, "EXECUTE"},
	{0x00000040, "DELETE_CHILD"},
	{0x00000080, "READ_ATTRIBUTES"},
	{0x00000100, "WRITE_ATTRIBUTES"},
	{0x00010000, "DELETE"},
	{0x00020000, "READ_CONTROL"},
	{0x00040000, "WRITE_DAC"},
	{0x00080000, "WRITE_OWNER"},
	{0x00100000, "SYNCHRONIZE"},
	{0x10000000, "GENERIC_ALL"},
	{0x20000000, "GENERIC_EXECUTE"},
	{0x40000000, "GENERIC_WRITE"},
	{0x80000000, "GENERIC_READ"},
}

func parseAccessMask(m uint32) []string {
	var out []string
	for _, b := range accessMaskBits {
		if m&b.bit != 0 {
			out = append(out, b.name)
		}
	}
	return out
}
