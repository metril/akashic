package metadata

import (
	"os/user"
	"strconv"
	"sync"
)

// OwnerResolver caches uid → username and gid → groupname lookups for a single
// scan. Failed lookups are remembered too so we don't retry them.
type OwnerResolver struct {
	mu       sync.Mutex
	users    map[uint32]string
	groups   map[uint32]string
}

// NewOwnerResolver creates a cache scoped to a single scan.
func NewOwnerResolver() *OwnerResolver {
	return &OwnerResolver{
		users:  make(map[uint32]string),
		groups: make(map[uint32]string),
	}
}

// User returns the username for uid, "" if unresolved.
func (r *OwnerResolver) User(uid uint32) string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if name, ok := r.users[uid]; ok {
		return name
	}
	u, err := user.LookupId(strconv.FormatUint(uint64(uid), 10))
	if err != nil {
		r.users[uid] = ""
		return ""
	}
	r.users[uid] = u.Username
	return u.Username
}

// Group returns the group name for gid, "" if unresolved.
func (r *OwnerResolver) Group(gid uint32) string {
	r.mu.Lock()
	defer r.mu.Unlock()
	if name, ok := r.groups[gid]; ok {
		return name
	}
	g, err := user.LookupGroupId(strconv.FormatUint(uint64(gid), 10))
	if err != nil {
		r.groups[gid] = ""
		return ""
	}
	r.groups[gid] = g.Name
	return g.Name
}
