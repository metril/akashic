package metadata

import (
	"errors"
	"os/exec"
	"strings"
	"sync/atomic"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

var getfaclMissing atomic.Bool

// CollectACL returns the POSIX ACL for a path by shelling out to `getfacl`.
//
// Returns nil (no error) when:
//   - getfacl is unavailable on the host (logged once via getfaclMissing flag)
//   - the filesystem doesn't carry ACLs for this path
//   - the entry has only the default three rwx entries (no extended ACL)
//
// We deliberately don't capture default ACLs (those that propagate to children)
// — only the active access ACL is recorded.
func CollectACL(path string) ([]models.ACLEntry, error) {
	if getfaclMissing.Load() {
		return nil, nil
	}
	cmd := exec.Command("getfacl", "--omit-header", "--absolute-names", "--skip-base", "--no-effective", path)
	out, err := cmd.Output()
	if err != nil {
		var execErr *exec.Error
		if errors.As(err, &execErr) && errors.Is(execErr.Err, exec.ErrNotFound) {
			getfaclMissing.Store(true)
			return nil, nil
		}
		// Permission denied / unsupported FS — quiet skip.
		return nil, nil
	}
	return parseACL(string(out)), nil
}

func parseACL(raw string) []models.ACLEntry {
	var entries []models.ACLEntry
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		// Lines look like: "user:alice:rwx" or "user::rwx" or "mask::r-x"
		parts := strings.SplitN(line, ":", 3)
		if len(parts) != 3 {
			continue
		}
		tag := parts[0]
		qualifier := parts[1]
		perms := parts[2]

		// _obj entries are user:: / group:: (no qualifier)
		if qualifier == "" {
			switch tag {
			case "user":
				tag = "user_obj"
			case "group":
				tag = "group_obj"
			}
		}
		entries = append(entries, models.ACLEntry{
			Tag:       tag,
			Qualifier: qualifier,
			Perms:     perms,
		})
	}
	if len(entries) == 0 {
		return nil
	}
	return entries
}
