// AIMD batch-size table tests (v0.29.2).
package scanner

import (
	"errors"
	"testing"
	"time"
)

func TestAdaptiveBatchSize_Grows(t *testing.T) {
	a := NewAdaptiveBatchSize(1000, 250, 5000, 100, 400)
	// Fast batches → additive increase by 250.
	if !a.Observe(50*time.Millisecond, nil) {
		t.Fatal("expected growth on 50ms latency, got no-change")
	}
	if got := a.Current(); got != 1250 {
		t.Errorf("after 1 fast batch: got %d, want 1250", got)
	}
	if !a.Observe(50*time.Millisecond, nil) {
		t.Fatal("expected further growth")
	}
	if got := a.Current(); got != 1500 {
		t.Errorf("after 2 fast batches: got %d, want 1500", got)
	}
}

func TestAdaptiveBatchSize_Halves(t *testing.T) {
	a := NewAdaptiveBatchSize(2000, 250, 5000, 100, 400)
	// Slow batch → multiplicative decrease by 2.
	if !a.Observe(800*time.Millisecond, nil) {
		t.Fatal("expected halving on 800ms latency")
	}
	if got := a.Current(); got != 1000 {
		t.Errorf("after slow batch: got %d, want 1000", got)
	}
}

func TestAdaptiveBatchSize_InBand_NoChange(t *testing.T) {
	a := NewAdaptiveBatchSize(1000, 250, 5000, 100, 400)
	if changed := a.Observe(250*time.Millisecond, nil); changed {
		t.Errorf("in-band latency triggered an adjustment")
	}
	if got := a.Current(); got != 1000 {
		t.Errorf("after in-band batch: got %d, want unchanged 1000", got)
	}
}

func TestAdaptiveBatchSize_ErrorHalvesAndClampsToFloor(t *testing.T) {
	a := NewAdaptiveBatchSize(400, 250, 5000, 100, 400)
	// Error halves 400 → 200, which is below floor 250 → clamp to 250.
	a.Observe(0, errors.New("boom"))
	if got := a.Current(); got != 250 {
		t.Errorf("after error from 400: got %d, want clamped to 250", got)
	}
	// Subsequent error from floor stays at floor (can't go lower).
	a.Observe(0, errors.New("again"))
	if got := a.Current(); got != 250 {
		t.Errorf("error at floor: got %d, want 250", got)
	}
}

func TestAdaptiveBatchSize_CeilingClamp(t *testing.T) {
	a := NewAdaptiveBatchSize(4900, 250, 5000, 100, 400)
	a.Observe(50*time.Millisecond, nil) // would be 5150 → clamp 5000
	if got := a.Current(); got != 5000 {
		t.Errorf("expected ceiling clamp: got %d", got)
	}
	// Already at ceiling — growth attempt is a no-op.
	if changed := a.Observe(50*time.Millisecond, nil); changed {
		t.Errorf("at ceiling, growth should not flag as changed")
	}
}

func TestAdaptiveBatchSize_PinByFloorEqualsCeiling(t *testing.T) {
	// floor == ceiling == initial pins the size — useful for ops who
	// want fixed sizing via env override.
	a := NewAdaptiveBatchSize(800, 800, 800, 100, 400)
	a.Observe(50*time.Millisecond, nil)
	a.Observe(800*time.Millisecond, nil)
	a.Observe(0, errors.New("e"))
	if got := a.Current(); got != 800 {
		t.Errorf("pinned size moved: got %d, want 800", got)
	}
}

func TestAdaptiveBatchSize_OnAdjustHook(t *testing.T) {
	a := NewAdaptiveBatchSize(1000, 250, 5000, 100, 400)
	var seen [][3]int64
	a.OnAdjust = func(prev, next int, latencyMs int64, err error) {
		seen = append(seen, [3]int64{int64(prev), int64(next), latencyMs})
	}
	a.Observe(50*time.Millisecond, nil)
	a.Observe(250*time.Millisecond, nil) // no change → no callback
	a.Observe(800*time.Millisecond, nil)
	if len(seen) != 2 {
		t.Fatalf("expected 2 adjustments logged; got %d (%v)", len(seen), seen)
	}
}

func TestAdaptiveBatchSize_NormalizesBadInputs(t *testing.T) {
	a := NewAdaptiveBatchSize(0, 0, 0, 0, 0)
	if got, want := a.Current(), 1; got != want {
		t.Errorf("degenerate inputs: current=%d, want=%d", got, want)
	}
	floor, ceiling := a.Range()
	if floor != 1 || ceiling != 1 {
		t.Errorf("degenerate range: got (%d,%d), want (1,1)", floor, ceiling)
	}
}
