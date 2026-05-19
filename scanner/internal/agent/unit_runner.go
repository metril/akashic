// Unit-coordinated scan runner (Phase 2 of v0.5.x parallel scanning).
//
// When MaxParallelScanners > 1 on a leased scan, the agent enters a
// lease loop over scan_work_units instead of running a single
// monolithic walk. Sibling scanners in the same pool — and, when the
// local AKASHIC_MAX_CONCURRENT_UNITS is raised, several worker
// goroutines within one scanner — cooperate on one source by draining
// a shared queue of budget-sized work units.
//
// The walk model (v0.34.0 — budget-bounded dynamic units):
//   - The first scanner to lease /work hits 204 and enqueues a single
//     root unit (path "").
//   - Each unit is walked by scanner.Run in budgeted shallow-split
//     mode: it walks its subtree breadth-first up to an entry budget,
//     then splits the un-walked frontier directories back into the
//     queue as fresh units. Idle sibling scanners steal those, so an
//     unbalanced tree still spreads evenly and coordination cost is
//     proportional to work, not directory count.
//   - The lease loop is "sticky": a 204 means "no unit right now", not
//     "exit" — the scanner keeps polling until the scan itself goes
//     terminal (a 409 scan-terminal).
//
// All shipped connectors (local, nfs, smb, s3) implement the
// optional ShallowWalker interface. A future connector that doesn't
// implement it falls back to the legacy single-walker path with a
// one-line warning.
package agent

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"log"
	"net/http"
	"path"
	"path/filepath"
	"sync"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/extract"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
)

func runUnitCoordinated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, leased *leasedScan,
) error {
	// Capability probe: build one connector solely to check it
	// implements ShallowWalker. A connector that doesn't (a future
	// type) falls back to the legacy single-walker path so the scan
	// still succeeds — just without parallelism. connectorFromLeased
	// does no I/O, so the throwaway is cheap.
	probeConn, err := connectorFromLeased(leased.Source)
	if err != nil {
		return err
	}
	if _, ok := probeConn.(connector.ShallowWalker); !ok {
		log.Printf(
			"scan %s: connector %q does not implement ShallowWalker; "+
				"falling back to legacy single-walker path",
			leased.ScanID, leased.Source.Type,
		)
		if err := runLeasedScan(ctx, cfg, priv, leased); err != nil {
			_ = complete(ctx, httpc, cfg, priv, leased.ScanID, "failed", err.Error())
			return err
		}
		return complete(ctx, httpc, cfg, priv, leased.ScanID, "completed", "")
	}

	root := sourceRoot(leased.Source)
	apiClient := client.New(cfg.APIBase, leased.APIJWT)

	// v0.30.0 — content extraction for the unit-coordinated path.
	// Built once and shared across this scanner's units (and workers);
	// each extraction pass builds its own connector instance via the
	// factory so SMB sessions stay isolated from the walk.
	extractor := extract.NewExtractor(cfg.TikaURL)
	extractFactory := func() (connector.Connector, error) {
		return connectorFromLeased(leased.Source)
	}

	// reporter + state are shared across all workers: State's counters
	// are atomic and LogSink.emit is a channel send, both safe under
	// concurrent walks, and one shared State means the per-scan
	// heartbeat reports the scan's aggregate progress.
	state := observe.NewState()
	reporter := observe.New(cfg.APIBase, leased.APIJWT, leased.ScanID, state)
	scanCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	reporter.SetUserCancel(cancel)
	reporter.Start(scanCtx)
	defer reporter.Stop()

	// Up-front enumeration runs once, before any worker starts: the
	// first scanner to arrive enqueues the single root unit. A scanner
	// that arrives once the queue already exists gets a unit leased to
	// it by the enumeration probe; that unit MUST be processed —
	// abandoning it used to orphan the unit and stall the whole scan
	// (v0.32.1) — so it is handed to worker 0 below.
	probeUnit, err := ensureUnitsEnumerated(scanCtx, httpc, cfg, priv, leased.ScanID)
	if err != nil {
		return fmt.Errorf("enumerate units: %w", err)
	}

	workers := cfg.MaxConcurrentUnits
	if workers < 1 {
		workers = 1
	}
	if workers > 1 {
		log.Printf("scan %s: draining work units with %d worker(s) in parallel",
			leased.ScanID, workers)
	}

	deps := unitWorkerDeps{
		httpc: httpc, cfg: cfg, priv: priv, apiClient: apiClient,
		leased: leased, root: root, state: state, reporter: reporter,
		extractor: extractor, extractFactory: extractFactory,
	}

	// Spawn the workers. Each runs the sticky lease loop with its own
	// connector; worker 0 starts with the probe-leased unit (if any).
	// runUnitCoordinated returns once every worker has exited — which
	// they all do on errScanTerminal or a scanCtx cancellation.
	var wg sync.WaitGroup
	errCh := make(chan error, workers)
	for i := 0; i < workers; i++ {
		var initial *workUnit
		if i == 0 {
			initial = probeUnit
		}
		wg.Add(1)
		go func(initial *workUnit) {
			defer wg.Done()
			if err := runUnitWorker(scanCtx, deps, initial); err != nil {
				errCh <- err
			}
		}(initial)
	}
	wg.Wait()
	close(errCh)
	// Return the first worker error, if any; a clean drain leaves errCh
	// empty and the receive yields nil.
	return <-errCh
}

// unitWorkerDeps bundles the immutable, shared dependencies a unit
// worker needs — passed by value so the goroutine closure stays small.
type unitWorkerDeps struct {
	httpc          *http.Client
	cfg            Config
	priv           ed25519.PrivateKey
	apiClient      *client.Client
	leased         *leasedScan
	root           string
	state          *observe.State
	reporter       *observe.Reporter
	extractor      *extract.Extractor
	extractFactory func() (connector.Connector, error)
}

// runUnitWorker drains the scan's work-unit queue with its own
// connector until the scan goes terminal. K of these run concurrently
// when MaxConcurrentUnits > 1; with the default of 1 it is exactly the
// v0.34.0 sticky lease loop.
func runUnitWorker(
	scanCtx context.Context, d unitWorkerDeps, pending *workUnit,
) error {
	conn, err := connectorFromLeased(d.leased.Source)
	if err != nil {
		return err
	}
	// Connect once for this worker's lifetime. Without it every per-unit
	// scanner.Run() would dial+auth+disconnect afresh — fine for
	// local/nfs (no-op), expensive for smb (auth handshake) and s3
	// (signed-request handshake amortised across the pool).
	if err := conn.Connect(scanCtx); err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer conn.Close()

	// Sticky lease loop. A 204 (errNoWork) means "no unit available
	// right now" — siblings or other workers may still be splitting
	// fresh units — so the worker stays attached and polls. It exits
	// only when the scan itself is terminal (errScanTerminal, a 409
	// scan-terminal) or the scan context is cancelled.
	for {
		if err := scanCtx.Err(); err != nil {
			return err
		}
		unit := pending
		pending = nil
		if unit == nil {
			u, lErr := leaseUnit(scanCtx, d.httpc, d.cfg, d.priv, d.leased.ScanID)
			if errors.Is(lErr, errScanTerminal) {
				log.Printf("scan %s: scan reached a terminal state; exiting unit loop", d.leased.ScanID)
				return nil
			}
			if errors.Is(lErr, errNoWork) {
				// No unit right now — stay attached and poll. The scan
				// answers 409 scan-terminal once it actually finalizes.
				sleepWithJitter(scanCtx, d.cfg.LeasePoll)
				continue
			}
			if lErr != nil {
				// Cap-reached or transient: jitter and try again. The cap
				// case clears as siblings finish their units.
				log.Printf("scan %s: lease unit: %v (sleeping)", d.leased.ScanID, lErr)
				sleepWithJitter(scanCtx, d.cfg.LeasePoll)
				continue
			}
			unit = u
		}

		// Heartbeat in the background; cancelled when the unit's walk returns.
		hbCtx, hbCancel := context.WithCancel(scanCtx)
		go heartbeatUnitLoop(hbCtx, d.httpc, d.cfg, d.priv, d.leased.ScanID, unit.ID)

		walkErr := runUnitWalk(scanCtx, d.httpc, d.cfg, d.priv, d.apiClient, conn, d.leased, d.root, unit, d.state, d.reporter, d.leased.Source.ExcludePatterns, d.extractor, d.extractFactory, d.cfg.ExtractWorkers)
		hbCancel()

		// Deliver the unit's terminal state, retrying on failure — see
		// deliverUnitTerminal. A dropped /complete leaves the unit
		// "running" until its lease expires and the API watchdog reaps
		// it, stalling scan finalization for ~2 min (v0.33.0).
		if walkErr != nil {
			// v0.34.0 — a transient stall (the SMB op guard force-closed
			// the connection) requeues the unit for retry rather than
			// permanently failing it and abandoning its subtree. The next
			// unit's scanner.Run reconnects the connector itself.
			requeue := false
			if ts, ok := conn.(connector.TransientStaller); ok && ts.IsStalled() {
				requeue = true
				log.Printf("scan %s unit %s: walk failed on a transient stall: %v (requeuing)",
					d.leased.ScanID, unit.ID, walkErr)
			} else {
				log.Printf("scan %s unit %s: walk failed: %v", d.leased.ScanID, unit.ID, walkErr)
			}
			deliverUnitTerminal("fail", d.leased.ScanID, unit.ID,
				func(ctx context.Context) error {
					return failUnit(ctx, d.httpc, d.cfg, d.priv, d.leased.ScanID, unit.ID, walkErr.Error(), requeue)
				})
			continue
		}
		deliverUnitTerminal("complete", d.leased.ScanID, unit.ID,
			func(ctx context.Context) error {
				return completeUnit(ctx, d.httpc, d.cfg, d.priv, d.leased.ScanID, unit.ID)
			})
	}
}

// terminalDeliveryAttempts bounds how hard a finished unit tries to tell
// the API it is done. A unit's terminal state MUST land: an undelivered
// /complete leaves the unit "running" until its lease expires and the API
// watchdog re-queues it, stalling scan finalization for ~2 min (v0.33.0).
const terminalDeliveryAttempts = 5

// terminalDeliveryBackoff is the first inter-attempt wait, doubled on each
// retry (1→2→4→8 s). A package var so tests can shrink it.
var terminalDeliveryBackoff = 1 * time.Second

// deliverUnitTerminal POSTs a unit's terminal state (`post` is completeUnit
// or failUnit pre-bound to its args), retrying with 1→2→4→8 s backoff.
//
// Each attempt gets its own fresh 5 s context rooted in Background — never
// the scanCtx — so a cancellation (Stop pressed, SIGTERM, heartbeat 409)
// can't abort delivery; if anything, a cancelled scan makes landing the
// unit's terminal state more urgent. After the budget is spent it gives
// up and logs: the API watchdog is the last-resort recovery.
func deliverUnitTerminal(name, scanID, unitID string, post func(context.Context) error) {
	backoff := terminalDeliveryBackoff
	for attempt := 1; attempt <= terminalDeliveryAttempts; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		err := post(ctx)
		cancel()
		if err == nil {
			return
		}
		if attempt == terminalDeliveryAttempts {
			log.Printf("scan %s unit %s: %s failed after %d attempts: %v "+
				"(API watchdog will recover the unit)",
				scanID, unitID, name, terminalDeliveryAttempts, err)
			return
		}
		log.Printf("scan %s unit %s: %s failed: %v (retry %d/%d in %s)",
			scanID, unitID, name, err, attempt, terminalDeliveryAttempts, backoff)
		time.Sleep(backoff)
		backoff *= 2
	}
}

// ensureUnitsEnumerated calls /work/lease once to probe for existing
// units and makes sure the scan's work queue exists:
//
//   - probe leases a unit  → the queue is already enumerated, and that
//     unit is returned for the CALLER to process. It must NOT be
//     abandoned: an abandoned leased unit sits "running" until its 60 s
//     lease expires, and if every scanner has drained the rest of the
//     queue by then nothing re-leases it and the scan never finalizes
//     (the v0.32.1 stall — the probe used to deliberately drop it).
//   - probe gets 204       → this scanner is the first to arrive; it
//     enqueues the single root unit (path "") and returns (nil, nil).
//     That unit does its own budgeted shallow walk + split when leased,
//     so the queue grows itself from there.
//   - probe gets 409 (cap) → units already exist but there is no slot
//     for us yet; return (nil, nil) and let the caller's lease loop
//     retry past the cap with backoff.
//   - probe gets 409 (scan-terminal) → the scan finished already;
//     return (nil, nil) and the lease loop exits on its next call.
func ensureUnitsEnumerated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID string,
) (*workUnit, error) {
	probe, err := leaseUnit(ctx, httpc, cfg, priv, scanID)
	if err == nil && probe != nil {
		log.Printf(
			"scan %s: queue already enumerated; processing probe-leased "+
				"unit %s", scanID, probe.ID,
		)
		return probe, nil
	}
	if err != nil && !errors.Is(err, errNoWork) {
		if errors.Is(err, errLeaseCap) {
			// Units exist (the cap is only enforced when a unit IS
			// available); we just have no slot yet. Skip enumeration —
			// the caller's lease loop retries past the cap.
			log.Printf("scan %s: queue already enumerated, lease capped; "+
				"entering lease loop", scanID)
			return nil, nil
		}
		if errors.Is(err, errScanTerminal) {
			// Scan finished before we even enumerated — nothing to do.
			return nil, nil
		}
		return nil, fmt.Errorf("probe lease: %w", err)
	}

	// 204 from probe → we're the first scanner. Enqueue the single root
	// unit; it does its own budgeted shallow walk + split when leased.
	res, err := splitUnits(ctx, httpc, cfg, priv, scanID, nil, []string{""})
	if err != nil {
		return nil, err
	}
	log.Printf("scan %s: enqueued root unit (created=%d, skipped=%d)",
		scanID, res.Created, res.Skipped)
	return nil, nil
}

// sourceRoot returns the per-type "root path" for a leased source.
// SMB sources don't have an explicit root in connection_config (the
// share itself is the root). S3 uses the bucket prefix.
func sourceRoot(src leasedSource) string {
	cfg := src.ConnectionConfig
	switch src.Type {
	case "local":
		return stringFromConfig(cfg, "path", "")
	case "nfs":
		return stringFromConfig(cfg, "export_path",
			stringFromConfig(cfg, "path", ""))
	case "smb":
		// go-smb2 paths are relative to the share root, so "" is the
		// right starting point.
		return ""
	case "s3":
		return stringFromConfig(cfg, "prefix", "")
	default:
		return stringFromConfig(cfg, "path", "")
	}
}

// joinSubpath builds the per-unit walk root from the source root + the
// unit's relative path. Uses native path separators for filesystem-
// flavoured connectors and forward-slash for SMB/S3.
func joinSubpath(srcType, root, sub string) string {
	if sub == "" {
		return root
	}
	switch srcType {
	case "smb", "s3":
		// Forward-slash semantics across the wire.
		if root == "" {
			return sub
		}
		return path.Join(root, sub)
	default:
		return filepath.Join(root, sub)
	}
}

// runUnitWalk walks a single leased unit via scanner.Run in budgeted
// shallow-split mode (v0.34.0): scanner.Run walks the unit's subtree
// breadth-first up to an entry budget, then calls the ShallowSplit
// closure with the un-walked frontier so it can be enqueued as fresh
// units. Every unit — including the "" root unit — goes through this one
// path, so all of scanner.Run's polish (incremental hashing, adaptive
// batching, content extraction, Live Log streaming) applies uniformly.
//
// scanner.Run connects/closes the connector itself per unit, so a unit
// following a transient SMB stall reconnects automatically.
func runUnitWalk(
	ctx context.Context,
	httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	apiClient *client.Client,
	conn connector.Connector,
	leased *leasedScan,
	root string,
	unit *workUnit,
	state *observe.State,
	reporter *observe.Reporter,
	excludes []string,
	extractor *extract.Extractor,
	extractFactory func() (connector.Connector, error),
	extractWorkers int,
) error {
	subRoot := joinSubpath(leased.Source.Type, root, unit.Path)
	srcType := leased.Source.Type
	scanID := leased.ScanID
	unitID := unit.ID
	unitPath := unit.Path

	// ShallowSplit hands the un-walked frontier (paths relative to this
	// unit's root) back to the queue as fresh units. Child paths are
	// scan-root-relative; chunked so a very wide directory doesn't make
	// one huge savepoint-per-path transaction on the API.
	shallowSplit := func(splitCtx context.Context, frontier []string) error {
		if len(frontier) == 0 {
			return nil
		}
		childPaths := make([]string, len(frontier))
		for i, rel := range frontier {
			childPaths[i] = joinSubpath(srcType, unitPath, rel)
		}
		const chunk = 500
		created, skipped := 0, 0
		for i := 0; i < len(childPaths); i += chunk {
			end := i + chunk
			if end > len(childPaths) {
				end = len(childPaths)
			}
			res, err := splitUnits(splitCtx, httpc, cfg, priv, scanID, nil, childPaths[i:end])
			if err != nil {
				return err
			}
			created += res.Created
			skipped += res.Skipped
		}
		log.Printf("scan %s unit %s: split %d child unit(s) (created=%d, skipped=%d)",
			scanID, unitID, len(childPaths), created, skipped)
		return nil
	}

	// v0.29.2 — adaptive batch sizing per unit. Each unit builds its own
	// batcher rather than sharing one across units; shared state would
	// couple a slow SMB unit to a fast local one and erase the per-unit
	// signal AIMD wants to act on.
	batcher := newAdaptiveBatcher(scanID)
	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        leased.Source.ID,
		ScanID:          scanID,
		Root:            subRoot,
		BatchSize:       500,
		AdaptiveBatcher: batcher,
		Hash:            leased.ScanType == "full",
		ExcludePatterns: excludes,
		State:           state,
		// v0.31.6 — wire the shared Reporter so this unit's walk streams
		// its log lines (connecting / walk progress / per-file current
		// path) to /api/scans/{id}/log and on into the Live Log.
		Reporter: reporter,
		// v0.30.0 — content extraction for this unit's subtree.
		Extractor:               extractor,
		ExtractConnectorFactory: extractFactory,
		ExtractWorkers:          extractWorkers,
		// v0.31.5 — one unit of a unit-coordinated scan, not a whole
		// scan: suppress the wire IsFinal flag so this unit's last batch
		// doesn't make the API finalize (and stale-sweep) the whole scan
		// while sibling units are still running. The scan is finalized
		// by the work-unit /complete path.
		SuppressScanFinal: true,
		// v0.34.0 — budgeted shallow-split mode; the closure enqueues the
		// un-walked frontier as fresh work units.
		ShallowSplit: shallowSplit,
		// v0.35.0 — entry budget per unit, resolved API-side from the
		// source/host scan_chunk_size. 0 (an older API) → scanner.Run
		// falls back to its own defaultShallowBudget.
		ShallowBudget: leased.Source.ScanChunkSize,
	})
	_, err := s.Run(ctx)
	return err
}

func heartbeatUnitLoop(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID string,
) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
		if err := heartbeatUnit(ctx, httpc, cfg, priv, scanID, unitID); err != nil {
			log.Printf("scan %s unit %s: heartbeat failed: %v", scanID, unitID, err)
		}
	}
}
