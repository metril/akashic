package agent

import "sync"

// RemoteConfig is the per-scanner config the API delivers (handshake +
// every heartbeat). The scanner has no local env/flag for these.
type RemoteConfig struct {
	TikaURL            string `json:"tika_url"`
	ExtractWorkers     int    `json:"extract_workers"`
	MaxConcurrentUnits int    `json:"max_concurrent_units"`
}

// LiveConfig holds the latest RemoteConfig; safe for concurrent use.
type LiveConfig struct {
	mu sync.RWMutex
	c  RemoteConfig
}

func (l *LiveConfig) Set(c RemoteConfig) {
	if c.MaxConcurrentUnits < 1 {
		c.MaxConcurrentUnits = 1
	}
	l.mu.Lock()
	l.c = c
	l.mu.Unlock()
}

func (l *LiveConfig) Get() RemoteConfig {
	l.mu.RLock()
	defer l.mu.RUnlock()
	return l.c
}

// Remote returns the current effective config: the live (API-delivered)
// one when present, else the static Config fields (used by tests).
func (c Config) Remote() RemoteConfig {
	if c.Live != nil {
		return c.Live.Get()
	}
	u := c.MaxConcurrentUnits
	if u < 1 {
		u = 1
	}
	return RemoteConfig{TikaURL: c.TikaURL, ExtractWorkers: c.ExtractWorkers, MaxConcurrentUnits: u}
}
