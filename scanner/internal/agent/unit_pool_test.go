package agent

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Regression: after shrinking 4->1 the pool must regrow to 4.
func TestUnitPool_RegrowsAfterShrink(t *testing.T) {
	var target atomic.Int32
	target.Store(4)
	var live, over atomic.Int32
	var done atomic.Bool
	var started sync.Map

	run := func(idx int, _ *workUnit, retire func(int) bool) error {
		if _, dup := started.LoadOrStore(idx, true); dup {
			started.Store(idx, true)
		}
		live.Add(1)
		defer live.Add(-1)
		for !done.Load() {
			if retire(idx) {
				return nil
			}
			if int(live.Load()) > int(target.Load())+3 {
				over.Add(1)
			}
			time.Sleep(2 * time.Millisecond)
		}
		return nil
	}
	p := newUnitPool(func() int { return int(target.Load()) }, run, func(int) {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p.start(ctx, 4, nil, 5*time.Millisecond)

	waitLive := func(n int32) {
		t.Helper()
		deadline := time.Now().Add(3 * time.Second)
		for live.Load() != n {
			if time.Now().After(deadline) {
				t.Fatalf("live=%d, want %d", live.Load(), n)
			}
			time.Sleep(2 * time.Millisecond)
		}
	}
	waitLive(4)
	target.Store(1)
	waitLive(1)
	target.Store(4)
	waitLive(4)
	// Hold steady: must not over-spawn.
	time.Sleep(50 * time.Millisecond)
	if live.Load() != 4 {
		t.Fatalf("over-spawn: live=%d", live.Load())
	}
	done.Store(true)
	p.wg.Wait()
	if over.Load() != 0 {
		t.Fatalf("over limit observed")
	}
}
