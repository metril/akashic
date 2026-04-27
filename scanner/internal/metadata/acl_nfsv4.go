package metadata

import (
	"errors"
	"os/exec"
	"strings"
	"sync/atomic"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

var nfs4GetfaclMissing atomic.Bool

func CollectNfsV4ACL(path string) (*models.ACL, error) {
	if nfs4GetfaclMissing.Load() {
		return nil, nil
	}
	cmd := exec.Command("nfs4_getfacl", path)
	out, err := cmd.Output()
	if err != nil {
		var execErr *exec.Error
		if errors.As(err, &execErr) && errors.Is(execErr.Err, exec.ErrNotFound) {
			nfs4GetfaclMissing.Store(true)
			return nil, nil
		}
		return nil, nil
	}
	entries := parseNfsV4ACL(string(out))
	if len(entries) == 0 {
		return nil, nil
	}
	return &models.ACL{
		Type:         "nfsv4",
		NfsV4Entries: entries,
	}, nil
}

var aceTypeMap = map[byte]string{
	'A': "allow",
	'D': "deny",
	'U': "audit",
	'L': "alarm",
}

var aceFlagMap = map[byte]string{
	'f': "file_inherit",
	'd': "dir_inherit",
	'n': "no_propagate",
	'i': "inherit_only",
	'S': "successful_access",
	'F': "failed_access",
	'g': "identifier_group",
	'I': "inherited",
}

var aceMaskLookup = map[byte]string{
	'r': "read_data",
	'w': "write_data",
	'a': "append_data",
	'x': "execute",
	'd': "delete",
	'D': "delete_child",
	't': "read_attributes",
	'T': "write_attributes",
	'n': "read_named_attrs",
	'N': "write_named_attrs",
	'c': "read_acl",
	'C': "write_acl",
	'o': "write_owner",
	'y': "synchronize",
}

func parseNfsV4ACL(raw string) []models.NfsV4ACE {
	var out []models.NfsV4ACE
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, ":", 4)
		if len(parts) != 4 {
			continue
		}
		typeChar := parts[0]
		flagsStr := parts[1]
		principal := parts[2]
		permsStr := parts[3]

		if len(typeChar) != 1 {
			continue
		}
		aceType, ok := aceTypeMap[typeChar[0]]
		if !ok {
			continue
		}
		flags := make([]string, 0, len(flagsStr))
		for i := 0; i < len(flagsStr); i++ {
			if name, ok := aceFlagMap[flagsStr[i]]; ok {
				flags = append(flags, name)
			}
		}
		mask := make([]string, 0, len(permsStr))
		for i := 0; i < len(permsStr); i++ {
			if name, ok := aceMaskLookup[permsStr[i]]; ok {
				mask = append(mask, name)
			}
		}
		out = append(out, models.NfsV4ACE{
			Principal: principal,
			AceType:   aceType,
			Flags:     flags,
			Mask:      mask,
		})
	}
	return out
}
