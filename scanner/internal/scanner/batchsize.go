// Adaptive batch sizing for ingest (v0.29.2).
//
// The pre-v0.29.2 scanner shipped a static BatchSize — 500 in v0.28.2,
// before that 1000. A static value is wrong for someone: a local NVMe
// source with small-xattr entries can sustain 5000+ per batch, while a
// remote SMB share with full ACLs barely survives 250. AdaptiveBatchSize
// converges on whatever the current source + API + Postgres + proxy
// stack can sustain — TCP-style additive-increase, multiplicative-
// decrease against a target latency window.
//
// The walker reads the current size from Current() on each batch-full
// check. The sender goroutine calls Observe(latency, err) after each
// SendBatch returns; that's the only place the value moves.
//
// Concurrency: Current() and Observe() are safe to call concurrently
// from different goroutines (the walker reads, the sender writes).
// Reads use atomic.LoadInt64; writes hold a mutex so a transient
// adjustment series doesn't race on the growth counter.
package scanner

import (
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

// errPayloadTooLarge is handed to OnAdjust when NotePayloadTooLarge
// fires, so the adjustment log line names the real cause (a 413).
var errPayloadTooLarge = errors.New("ingest body limit hit (413)")

// AdaptiveBatchSize implements AIMD against a target latency window.
//
// Tuning rationale:
//   - Initial 1000: matches the v0.27 default, sized so a small-xattr
//     batch is well under the 32 MB nginx ceiling but big enough to
//     amortise per-request overhead.
//   - Floor 250: below this, per-request overhead dominates throughput
//     and TCP slow-start dominates wall time. SMB with heavy ACLs has
//     been observed to land here.
//   - Ceiling 5000: chosen so a heavy-ACL batch (~5 KB/row) still fits
//     in nginx's 32 MB body cap with comfortable headroom.
//   - Target 100–400 ms: below 100 ms means we're under-utilising the
//     pipe; above 400 ms means the walker is starving. Inside that
//     band, hold steady — perturbations have a cost.
//   - +250 increment: ~25% of initial; small enough that one
//     transient slowdown doesn't blow us back to floor.
//   - ÷2 decrement on overshoot: aggressive on the way down (TCP
//     congestion control logic — once we hit a wall, get clear of it
//     fast).
//   - ÷2 + clamp to floor on error: an HTTP 5xx or timeout means
//     "this batch was too much"; treat as the most aggressive signal.
type AdaptiveBatchSize struct {
	mu      sync.Mutex
	current int64 // atomic.LoadInt64 for reads; mu held for writes

	floor          int
	ceiling        int
	targetLowMs    int
	targetHighMs   int
	growthStep     int

	// Logger callback for adjustments. nil = silent.
	OnAdjust func(prev, next int, latencyMs int64, err error)
}

// NewAdaptiveBatchSize returns a configured batcher. Callers can pin
// the size by setting floor == ceiling == initial.
func NewAdaptiveBatchSize(initial, floor, ceiling, targetLowMs, targetHighMs int) *AdaptiveBatchSize {
	if floor < 1 {
		floor = 1
	}
	if ceiling < floor {
		ceiling = floor
	}
	if initial < floor {
		initial = floor
	}
	if initial > ceiling {
		initial = ceiling
	}
	if targetLowMs < 0 {
		targetLowMs = 0
	}
	if targetHighMs <= targetLowMs {
		targetHighMs = targetLowMs + 1
	}
	return &AdaptiveBatchSize{
		current:      int64(initial),
		floor:        floor,
		ceiling:      ceiling,
		targetLowMs:  targetLowMs,
		targetHighMs: targetHighMs,
		growthStep:   250,
	}
}

// Current returns the batch size to use for the next batch. Safe to
// call from any goroutine — backed by an atomic load.
func (a *AdaptiveBatchSize) Current() int {
	return int(atomic.LoadInt64(&a.current))
}

// Observe records the latency of the most recent SendBatch and adjusts
// the working size. Returns true when the value changed (for logging).
//
// Decision tree:
//   - err non-nil: ÷2 + clamp to floor.
//   - latency > targetHighMs: ÷2.
//   - latency < targetLowMs: +growthStep.
//   - in band: no change.
func (a *AdaptiveBatchSize) Observe(latency time.Duration, err error) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	prev := int(a.current)
	next := prev
	latencyMs := latency.Milliseconds()

	switch {
	case err != nil:
		// Errors are the strongest signal: half + floor clamp.
		next = prev / 2
		if next < a.floor {
			next = a.floor
		}
	case latencyMs > int64(a.targetHighMs):
		next = prev / 2
		if next < a.floor {
			next = a.floor
		}
	case latencyMs < int64(a.targetLowMs):
		next = prev + a.growthStep
		if next > a.ceiling {
			next = a.ceiling
		}
	}

	if next == prev {
		return false
	}
	atomic.StoreInt64(&a.current, int64(next))
	if a.OnAdjust != nil {
		a.OnAdjust(prev, next, latencyMs, err)
	}
	return true
}

// NotePayloadTooLarge records that the batch at the current size hit a
// hard request-body limit — a 413 the SendBatch client recovered from
// by splitting the batch (v0.30.1). Unlike Observe's latency-driven
// ÷2, this also lowers the *ceiling* below the offending size, so AIMD
// can never grow back into the same wall. Without it, every large
// batch would oscillate into a 413 and pay the split cost forever.
// Returns true when the working size changed (for logging).
func (a *AdaptiveBatchSize) NotePayloadTooLarge() bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	prev := int(a.current)
	next := prev / 2
	if next < a.floor {
		next = a.floor
	}
	// Pin the ceiling below the size that just 413'd, even if the
	// working size was already at the floor.
	a.ceiling = next
	if next == prev {
		return false
	}
	atomic.StoreInt64(&a.current, int64(next))
	if a.OnAdjust != nil {
		a.OnAdjust(prev, next, 0, errPayloadTooLarge)
	}
	return true
}

// Range returns (floor, ceiling) for diagnostic output.
func (a *AdaptiveBatchSize) Range() (int, int) { return a.floor, a.ceiling }

// String renders the current state for log lines.
func (a *AdaptiveBatchSize) String() string {
	return fmt.Sprintf(
		"AdaptiveBatchSize{current=%d floor=%d ceiling=%d target=%d-%dms}",
		a.Current(), a.floor, a.ceiling, a.targetLowMs, a.targetHighMs,
	)
}
