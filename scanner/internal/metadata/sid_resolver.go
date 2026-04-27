package metadata

import "sync"

// SIDLookuper is satisfied by *lsarpc.Client (via wrapper) or any test stub.
type SIDLookuper interface {
	Lookup(sid string) string
}

// SIDResolver layers well-known + cache + LSA fallback. Safe for concurrent use.
type SIDResolver struct {
	mu       sync.Mutex
	cache    map[string]string
	fallback SIDLookuper
}

func NewSIDResolver(fallback SIDLookuper) *SIDResolver {
	return &SIDResolver{
		cache:    make(map[string]string),
		fallback: fallback,
	}
}

func (r *SIDResolver) Lookup(sid string) string {
	if name := WellKnownSIDName(sid); name != "" {
		return name
	}
	r.mu.Lock()
	if v, ok := r.cache[sid]; ok {
		r.mu.Unlock()
		return v
	}
	r.mu.Unlock()
	var name string
	if r.fallback != nil {
		name = r.fallback.Lookup(sid)
	}
	r.mu.Lock()
	r.cache[sid] = name
	r.mu.Unlock()
	return name
}
