package metadata

import (
	"sync"
	"testing"
)

type fakeLookup struct {
	mu    sync.Mutex
	calls int
	table map[string]string
}

func (f *fakeLookup) Lookup(sid string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	return f.table[sid]
}

func TestSIDResolver_WellKnownNoFallback(t *testing.T) {
	r := NewSIDResolver(&fakeLookup{table: map[string]string{}})
	if got := r.Lookup("S-1-5-18"); got != "NT AUTHORITY\\SYSTEM" {
		t.Errorf("got %q", got)
	}
}

func TestSIDResolver_DomainSIDFallsBackToFake(t *testing.T) {
	f := &fakeLookup{table: map[string]string{"S-1-5-21-1-2-3-4": "DOMAIN\\alice"}}
	r := NewSIDResolver(f)
	if got := r.Lookup("S-1-5-21-1-2-3-4"); got != "DOMAIN\\alice" {
		t.Errorf("got %q", got)
	}
	r.Lookup("S-1-5-21-1-2-3-4")
	if f.calls != 1 {
		t.Errorf("expected 1 fallback call, got %d", f.calls)
	}
}

func TestSIDResolver_NilFallbackOK(t *testing.T) {
	r := NewSIDResolver(nil)
	if got := r.Lookup("S-1-5-21-9-9-9-9"); got != "" {
		t.Errorf("expected empty, got %q", got)
	}
}
