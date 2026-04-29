// Package observe ships live progress, structured logs, and raw stderr
// to the API while a scan is running.
//
// Three independent goroutines, all sharing the same Reporter:
//
//   - heartbeat: every 1 s, snapshots the State counters and POSTs to
//     /api/scans/{id}/heartbeat. Cheap; the scan never blocks on it.
//   - log drain: reads from the LogSink's bounded channel, batches
//     10 lines or 500 ms, POSTs to /api/scans/{id}/log.
//   - stderr relay: reads from a pipe substituted for os.Stderr at scan
//     start; batches 4 KB or 200 ms, POSTs to /api/scans/{id}/stderr.
//
// Failures on any channel are logged and dropped — the scan must never
// abort because progress reporting hit a transient network blip. The
// final batch ingest is the source of truth for completion data.
package observe

import (
	"context"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
)

// State holds the counters the heartbeat snapshots. All fields are atomic
// so the walker callback (hot path) can update without locks.
type State struct {
	filesScanned   atomic.Int64
	bytesScanned   atomic.Int64
	filesSkipped   atomic.Int64
	dirsWalked     atomic.Int64
	dirsQueued     atomic.Int64
	totalEstimated atomic.Int64 // 0 = unset (matches "no prewalk" signal)

	// currentPath and phase change less frequently — guarded by a single
	// mutex rather than atomic.Value so the heartbeat sees a coherent
	// snapshot of both at once.
	mu          sync.RWMutex
	currentPath string
	phase       string
}

func NewState() *State { return &State{} }

func (s *State) IncFile()                  { s.filesScanned.Add(1) }
func (s *State) AddBytes(n int64)          { s.bytesScanned.Add(n) }
func (s *State) IncSkipped()               { s.filesSkipped.Add(1) }
func (s *State) IncDirWalked()             { s.dirsWalked.Add(1) }
func (s *State) SetDirsQueued(n int64)     { s.dirsQueued.Store(n) }
func (s *State) SetTotalEstimated(n int64) { s.totalEstimated.Store(n) }

func (s *State) SetCurrent(path, phase string) {
	s.mu.Lock()
	if path != "" {
		s.currentPath = path
	}
	if phase != "" {
		s.phase = phase
	}
	s.mu.Unlock()
}

type snapshot struct {
	CurrentPath    string `json:"current_path,omitempty"`
	FilesScanned   int64  `json:"files_scanned"`
	BytesScanned   int64  `json:"bytes_scanned"`
	FilesSkipped   int64  `json:"files_skipped"`
	DirsWalked     int64  `json:"dirs_walked"`
	DirsQueued     int64  `json:"dirs_queued"`
	TotalEstimated *int64 `json:"total_estimated,omitempty"`
	Phase          string `json:"phase,omitempty"`
}

func (s *State) snapshot() snapshot {
	s.mu.RLock()
	cp, ph := s.currentPath, s.phase
	s.mu.RUnlock()
	out := snapshot{
		CurrentPath:  cp,
		FilesScanned: s.filesScanned.Load(),
		BytesScanned: s.bytesScanned.Load(),
		FilesSkipped: s.filesSkipped.Load(),
		DirsWalked:   s.dirsWalked.Load(),
		DirsQueued:   s.dirsQueued.Load(),
		Phase:        ph,
	}
	if t := s.totalEstimated.Load(); t > 0 {
		out.TotalEstimated = &t
	}
	return out
}

// Reporter coordinates the three goroutines. Start() launches them,
// Stop() drains and waits.
type Reporter struct {
	apiURL  string
	apiKey  string
	scanID  string
	state   *State
	logSink *LogSink

	httpClient *http.Client

	// Stop coordination.
	cancel context.CancelFunc
	done   chan struct{}
}

// New builds a Reporter. apiURL is the base (e.g., "http://api:8000"),
// apiKey is the bearer token, scanID is the running scan's UUID. The
// caller still has to start the goroutines via Start(); split so callers
// can grab the LogSink before goroutines run (so early log lines aren't
// lost in a startup race).
func New(apiURL, apiKey, scanID string, state *State) *Reporter {
	return &Reporter{
		apiURL:  apiURL,
		apiKey:  apiKey,
		scanID:  scanID,
		state:   state,
		logSink: newLogSink(),
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
		done: make(chan struct{}),
	}
}

// LogSink returns the sink that callers should use for structured log
// lines. Safe to use before Start() — lines just queue up.
func (r *Reporter) LogSink() *LogSink { return r.logSink }

// Start launches the heartbeat goroutine and the log-drain goroutine.
// stderr relay is opt-in via StartStderrRelay since it replaces os.Stderr
// process-wide and isn't safe in tests.
func (r *Reporter) Start(ctx context.Context) {
	ctx, cancel := context.WithCancel(ctx)
	r.cancel = cancel
	go func() {
		defer close(r.done)
		var wg sync.WaitGroup
		wg.Add(2)
		go func() { defer wg.Done(); r.runHeartbeat(ctx) }()
		go func() { defer wg.Done(); r.runLogDrain(ctx) }()
		wg.Wait()
	}()
}

// Stop signals the goroutines to exit and waits for them to drain.
// Best-effort — if Stop is called before Start, it's a no-op.
func (r *Reporter) Stop() {
	if r.cancel == nil {
		return
	}
	r.cancel()
	// LogSink close drains any buffered lines before the drain goroutine
	// exits its select loop.
	r.logSink.close()
	<-r.done
}
