package connector

import (
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestSSHConnector_ACLCacheLookup(t *testing.T) {
	c := &SSHConnector{aclCache: map[string]*models.ACL{
		"/tmp/foo": {Type: "posix", Entries: []models.PosixACE{{Tag: "user_obj", Perms: "rwx"}}},
	}}
	if got := c.aclCache["/tmp/foo"]; got == nil || got.Type != "posix" {
		t.Errorf("expected cached posix ACL, got %+v", got)
	}
	if got := c.aclCache["/tmp/missing"]; got != nil {
		t.Errorf("expected nil for uncached path, got %+v", got)
	}
}
