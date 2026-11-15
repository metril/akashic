package metadata

import "github.com/akashic-project/akashic/scanner/internal/apiclient"

// CollectACL tries NFSv4 first (more expressive), falls back to POSIX.
// Returns nil when neither tool yields an extended ACL.
func CollectACL(path string) (*apiclient.ACLInput, error) {
	if acl, err := CollectNfsV4ACL(path); err == nil && acl != nil {
		return acl, nil
	}
	return CollectPosixACL(path)
}
