// Coverage for deliverUnitTerminal (v0.33.0).
//
// A finished work unit MUST land its /complete (or /fail) POST. Pre-fix
// these were best-effort: one transient failure orphaned the unit in
// "running" until its lease expired and the API watchdog re-queued it,
// stalling scan finalization for ~2 min. deliverUnitTerminal retries with
// backoff; these tests pin that contract.
package agent

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestDeliverUnitTerminal_SucceedsAfterTransientFailures(t *testing.T) {
	old := terminalDeliveryBackoff
	terminalDeliveryBackoff = time.Millisecond
	defer func() { terminalDeliveryBackoff = old }()

	calls := 0
	deliverUnitTerminal("complete", "scan-1", "unit-1", func(context.Context) error {
		calls++
		if calls < 3 {
			return errors.New("connection reset")
		}
		return nil
	})
	if calls != 3 {
		t.Fatalf("expected 3 attempts (2 failures + 1 success), got %d", calls)
	}
}

func TestDeliverUnitTerminal_StopsAtTheAttemptBudget(t *testing.T) {
	old := terminalDeliveryBackoff
	terminalDeliveryBackoff = time.Millisecond
	defer func() { terminalDeliveryBackoff = old }()

	calls := 0
	deliverUnitTerminal("complete", "scan-1", "unit-1", func(context.Context) error {
		calls++
		return errors.New("api unreachable")
	})
	if calls != terminalDeliveryAttempts {
		t.Fatalf("a never-succeeding post should be tried exactly %d times, got %d",
			terminalDeliveryAttempts, calls)
	}
}

func TestDeliverUnitTerminal_FirstAttemptSucceeds(t *testing.T) {
	calls := 0
	start := time.Now()
	deliverUnitTerminal("complete", "scan-1", "unit-1", func(context.Context) error {
		calls++
		return nil
	})
	if calls != 1 {
		t.Fatalf("a clean post should be tried once, got %d", calls)
	}
	if elapsed := time.Since(start); elapsed > time.Second {
		t.Errorf("a first-try success must not sleep; took %s", elapsed)
	}
}

// Each attempt must get a non-nil, non-cancelled context — the retry
// helper roots fresh Background contexts so a cancelled scan can't abort
// terminal delivery.
func TestDeliverUnitTerminal_PassesLiveContext(t *testing.T) {
	deliverUnitTerminal("complete", "scan-1", "unit-1", func(ctx context.Context) error {
		if ctx == nil {
			t.Fatal("nil context passed to the terminal post")
		}
		if err := ctx.Err(); err != nil {
			t.Fatalf("context already done: %v", err)
		}
		return nil
	})
}
