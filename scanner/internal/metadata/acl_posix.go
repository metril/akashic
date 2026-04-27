package metadata

import (
	"errors"
	"os/exec"
	"strings"
	"sync/atomic"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

var getfaclMissing atomic.Bool

// CollectPosixACL returns the access + default POSIX ACL for a path by shelling
// out to `getfacl`. Returns nil (no error) when getfacl is unavailable or the
// filesystem doesn't carry ACLs.
func CollectPosixACL(path string) (*models.ACL, error) {
	if getfaclMissing.Load() {
		return nil, nil
	}
	cmd := exec.Command("getfacl", "--omit-header", "--absolute-names", path)
	out, err := cmd.Output()
	if err != nil {
		var execErr *exec.Error
		if errors.As(err, &execErr) && errors.Is(execErr.Err, exec.ErrNotFound) {
			getfaclMissing.Store(true)
			return nil, nil
		}
		return nil, nil
	}
	access, defaults := parsePosixACL(string(out))
	if access == nil && defaults == nil {
		return nil, nil
	}
	return &models.ACL{
		Type:           "posix",
		Entries:        access,
		DefaultEntries: defaults,
	}, nil
}

// CollectACL is re-introduced in Task 2.3 as the dispatcher. Stub here keeps
// the package buildable during Phase 2 transition.
func CollectACL(path string) (*models.ACL, error) {
	return CollectPosixACL(path)
}

func parsePosixACL(raw string) (access, defaults []models.PosixACE) {
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		isDefault := false
		if strings.HasPrefix(line, "default:") {
			isDefault = true
			line = strings.TrimPrefix(line, "default:")
		}
		parts := strings.SplitN(line, ":", 3)
		if len(parts) != 3 {
			continue
		}
		tag, qualifier, perms := parts[0], parts[1], parts[2]
		if qualifier == "" {
			switch tag {
			case "user":
				tag = "user_obj"
			case "group":
				tag = "group_obj"
			}
		}
		ace := models.PosixACE{Tag: tag, Qualifier: qualifier, Perms: perms}
		if isDefault {
			defaults = append(defaults, ace)
		} else {
			access = append(access, ace)
		}
	}
	return access, defaults
}
