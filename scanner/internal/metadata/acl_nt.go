package metadata

import (
	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/metadata/sddl"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// SidNamer is implemented by Phase 9's LSARPC resolver. Phase 8 passes nil and
// only well-known SIDs are resolved.
type SidNamer interface {
	Lookup(sid string) string
}

// SDToNtACL converts a binary security descriptor to a wrapped NT ACL.
// `namer` is optional — when nil, only well-known SIDs are resolved.
func SDToNtACL(sd []byte, namer SidNamer) (*apiclient.ACLInput, error) {
	parsed, err := sddl.ParseSecurityDescriptor(sd)
	if err != nil {
		return nil, err
	}
	out := apiclient.NtACLInput{Control: wire.Strs(parsed.Control)}
	if parsed.OwnerSID != "" {
		out.Owner = &apiclient.NtPrincipalInput{
			Sid:  parsed.OwnerSID,
			Name: wire.Str(resolveSID(parsed.OwnerSID, namer)),
		}
	}
	if parsed.GroupSID != "" {
		out.Group = &apiclient.NtPrincipalInput{
			Sid:  parsed.GroupSID,
			Name: wire.Str(resolveSID(parsed.GroupSID, namer)),
		}
	}
	for _, ace := range parsed.DaclEntries {
		out.Entries = append(out.Entries, apiclient.NtACEInput{
			Sid:     ace.SID,
			Name:    wire.Str(resolveSID(ace.SID, namer)),
			AceType: apiclient.NtACEInputAceType(ace.AceType),
			Flags:   wire.Strs(ace.Flags),
			Mask:    wire.Strs(ace.Mask),
		})
	}
	return wire.NtACL(out), nil
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
