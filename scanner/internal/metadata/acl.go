package metadata

import "github.com/akashic-project/akashic/scanner/pkg/models"

// CollectACL is the canonical entry-point for local ACL capture. It tries
// NFSv4 first (added in Phase 4) and falls back to POSIX. Returns nil when
// neither tool yields an extended ACL.
func CollectACL(path string) (*models.ACL, error) {
	// Phase 4 adds: if acl, err := CollectNfsV4ACL(path); err == nil && acl != nil { return acl, nil }
	return CollectPosixACL(path)
}
