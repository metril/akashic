package sddl

import (
	"encoding/binary"
	"fmt"
	"strings"
)

// ParseSID decodes a binary SID (MS-DTYP §2.4.2.2) into its string form.
func ParseSID(b []byte) (string, int, error) {
	if len(b) < 8 {
		return "", 0, fmt.Errorf("sid: too short (%d bytes)", len(b))
	}
	if b[0] != 1 {
		return "", 0, fmt.Errorf("sid: unsupported revision %d", b[0])
	}
	subCount := int(b[1])
	need := 8 + subCount*4
	if len(b) < need {
		return "", 0, fmt.Errorf("sid: truncated (need %d have %d)", need, len(b))
	}
	var auth uint64
	for _, c := range b[2:8] {
		auth = auth<<8 | uint64(c)
	}
	parts := []string{"S-1", fmt.Sprintf("%d", auth)}
	for i := 0; i < subCount; i++ {
		sub := binary.LittleEndian.Uint32(b[8+i*4 : 8+i*4+4])
		parts = append(parts, fmt.Sprintf("%d", sub))
	}
	return strings.Join(parts, "-"), need, nil
}
