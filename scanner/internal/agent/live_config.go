package agent

import (
	"sync"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// LiveConfig holds the latest per-scanner config the API delivers
// (handshake + every heartbeat); the scanner has no local env/flag for
// these. Safe for concurrent use.
type LiveConfig struct {
	mu sync.RWMutex
	c  apiclient.ScannerEffectiveConfig
}

func (l *LiveConfig) Set(c apiclient.ScannerEffectiveConfig) {
	if c.MaxConcurrentUnits < 1 {
		c.MaxConcurrentUnits = 1
	}
	l.mu.Lock()
	l.c = c
	l.mu.Unlock()
}

func (l *LiveConfig) Get() apiclient.ScannerEffectiveConfig {
	l.mu.RLock()
	defer l.mu.RUnlock()
	return l.c
}

// Remote returns the current effective config: the live (API-delivered)
// one when present, else the static Config fields (used by tests).
func (c Config) Remote() apiclient.ScannerEffectiveConfig {
	if c.Live != nil {
		return c.Live.Get()
	}
	u := c.MaxConcurrentUnits
	if u < 1 {
		u = 1
	}
	return apiclient.ScannerEffectiveConfig{TikaUrl: c.TikaURL, ExtractWorkers: c.ExtractWorkers, MaxConcurrentUnits: u}
}
