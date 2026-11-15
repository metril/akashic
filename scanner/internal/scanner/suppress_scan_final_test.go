// Options.SuppressScanFinal wire-flag behaviour (v0.31.5).
//
// A unit-coordinated (multi-scanner) scan walks each non-root work
// unit through the single-scanner scanner.Run path. That walk's last
// batch is final only for its *unit*, not the scan — sending
// IsFinal=true makes the API complete (and stale-sweep) the whole scan
// after its first unit, truncating it. SuppressScanFinal forces the
// wire flag to false; the inaccessible-count totals must still ride
// the last batch (the API reads those independent of IsFinal).
package scanner

import (
	"context"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// inaccessibleWalkConn emits zero entries but reports a non-zero
// inaccessible count, so a test can assert those totals still ride the
// final batch even when its IsFinal flag is suppressed.
type inaccessibleWalkConn struct{ emptyWalkConn }

func (inaccessibleWalkConn) Walk(
	_ context.Context, _ string, _ []string, _ bool, _ bool,
	_ func(*apiclient.EntryIn) error,
) (walker.WalkStats, error) {
	return walker.WalkStats{InaccessibleDirs: 2, InaccessibleFiles: 3}, nil
}

func runCapturingLastBatch(t *testing.T, suppress bool) apiclient.ScanBatchIn {
	t.Helper()
	var last apiclient.ScanBatchIn
	var seen bool
	srv := newTestServer(t, func(b apiclient.ScanBatchIn) {
		last = b
		seen = true
	})
	defer srv.Close()

	s := New(mustClient(t, srv.URL, "k"), inaccessibleWalkConn{}, Options{
		SourceID: testSource, ScanID: testScan, Root: "/", BatchSize: 10,
		SuppressScanFinal: suppress,
	})
	if _, err := s.Run(context.Background()); err != nil {
		t.Fatalf("Run failed: %v", err)
	}
	if !seen {
		t.Fatal("server saw no batch POST")
	}
	return last
}

func TestSuppressScanFinal_ForcesIsFinalFalse(t *testing.T) {
	b := runCapturingLastBatch(t, true)
	if wire.Deref(b.IsFinal) {
		t.Error("SuppressScanFinal=true: final batch still wired IsFinal=true")
	}
	// The inaccessible-count totals must still ride the last batch —
	// they are decoupled from the wire IsFinal flag.
	if wire.Deref(b.InaccessibleDirs) != 2 || wire.Deref(b.InaccessibleFiles) != 3 {
		t.Errorf("inaccessible counts lost on suppressed final batch: dirs=%d files=%d",
			wire.Deref(b.InaccessibleDirs), wire.Deref(b.InaccessibleFiles))
	}
}

func TestSuppressScanFinal_DefaultSendsIsFinalTrue(t *testing.T) {
	b := runCapturingLastBatch(t, false)
	if !wire.Deref(b.IsFinal) {
		t.Error("SuppressScanFinal=false: final batch should wire IsFinal=true")
	}
}
