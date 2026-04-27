package metadata

import (
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// CollectACL is a stub during the Phase 2 transition. Task 2.2 replaces this
// with a real POSIX ACL collector that returns the wrapped *models.ACL shape.
func CollectACL(path string) (*models.ACL, error) {
	return nil, nil
}
