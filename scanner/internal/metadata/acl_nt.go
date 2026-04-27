package metadata

import (
	"github.com/akashic-project/akashic/scanner/internal/metadata/sddl"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// SidNamer is implemented by Phase 9's LSARPC resolver. Phase 8 passes nil and
// only well-known SIDs are resolved.
type SidNamer interface {
	Lookup(sid string) string
}

// SDToNtACL converts a binary security descriptor to a wrapped NT ACL.
// `namer` is optional — when nil, only well-known SIDs are resolved.
func SDToNtACL(sd []byte, namer SidNamer) (*models.ACL, error) {
	parsed, err := sddl.ParseSecurityDescriptor(sd)
	if err != nil {
		return nil, err
	}
	out := &models.ACL{
		Type:    "nt",
		Control: parsed.Control,
	}
	if parsed.OwnerSID != "" {
		out.Owner = &models.NtPrincipal{
			Sid:  parsed.OwnerSID,
			Name: resolveSID(parsed.OwnerSID, namer),
		}
	}
	if parsed.GroupSID != "" {
		out.Group = &models.NtPrincipal{
			Sid:  parsed.GroupSID,
			Name: resolveSID(parsed.GroupSID, namer),
		}
	}
	for _, ace := range parsed.DaclEntries {
		out.NtEntries = append(out.NtEntries, models.NtACE{
			Sid:     ace.SID,
			Name:    resolveSID(ace.SID, namer),
			AceType: ace.AceType,
			Flags:   ace.Flags,
			Mask:    ace.Mask,
		})
	}
	return out, nil
}

func resolveSID(sid string, namer SidNamer) string {
	if name := WellKnownSIDName(sid); name != "" {
		return name
	}
	if namer != nil {
		return namer.Lookup(sid)
	}
	return ""
}
