// Unit-coordinated scan runner (Phase 2 of v0.5.x parallel scanning).
//
// When MaxParallelScanners > 1 on a leased scan, the agent enters a
// lease loop over scan_work_units instead of running a single
// monolithic walk. Sibling scanners in the same pool cooperate on
// one source — each claims a different top-level subtree.
//
// The walk model is intentionally simple:
//   - The first scanner to lease /work hits 204 (no units yet); it
//     calls connector.WalkShallow on the source root, splits off the
//     immediate subdirectories as units, plus a special "" unit for
//     root-level files, then re-leases.
//   - The "" unit re-runs WalkShallow to emit root-level files (the
//     subdirs returned this time are already in the queue).
//   - Every other unit performs a full recursive walk of the source
//     root + unit.path subtree using the existing scanner.Run path.
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
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/extract"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func runUnitCoordinated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, leased *leasedScan,
) error {
	conn, err := connectorFromLeased(leased.Source)
	if err != nil {
		return err
	}

	// Type-assert ShallowWalker. If a (future) connector doesn't
	// implement it, fall back to the legacy single-walker path so the
	// scan still succeeds — just without parallelism.
	shallow, ok := conn.(connector.ShallowWalker)
	if !ok {
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
	// Built once and shared across this scanner's units; each
	// extraction pass builds its own connector instance via the
	// factory so SMB sessions stay isolated from the walk.
	extractor := extract.NewExtractor(cfg.TikaURL)
	extractFactory := func() (connector.Connector, error) {
		return connectorFromLeased(leased.Source)
	}

	state := observe.NewState()
	reporter := observe.New(cfg.APIBase, leased.APIJWT, leased.ScanID, state)
	scanCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	reporter.SetUserCancel(cancel)
	reporter.Start(scanCtx)
	defer reporter.Stop()

	// Connect once for the whole unit-loop lifetime. Without this each
	// per-unit scanner.Run() would dial+auth+disconnect afresh — fine
	// for local/nfs (no-op), expensive for smb (auth handshake) and
	// s3 (signed-request handshake amortised across the pool).
	if err := conn.Connect(scanCtx); err != nil {
		return fmt.Errorf("connect: %w", err)
	}
	defer conn.Close()

	// Up-front enumeration: if no units exist yet for this scan, the
	// first scanner to arrive splits the source into a "root files" unit
	// (path "") plus one unit per top-level subdirectory. A scanner that
	// arrives once the queue already exists gets a unit leased to it by
	// the enumeration probe; that unit is returned here and MUST be
	// processed below — abandoning it used to orphan the unit and stall
	// the whole scan (v0.32.1).
	probeUnit, err := ensureUnitsEnumerated(scanCtx, httpc, cfg, priv, leased.ScanID, root, shallow, leased.Source.ExcludePatterns)
	if err != nil {
		return fmt.Errorf("enumerate units: %w", err)
	}

	// Lease loop. Stops when /work/lease returns 204 (no work for me).
	// `pending` carries the probe-leased unit (if any) so the first
	// iteration processes it instead of leasing a fresh one.
	pending := probeUnit
	for {
		if err := scanCtx.Err(); err != nil {
			return err
		}
		unit := pending
		pending = nil
		if unit == nil {
			u, lErr := leaseUnit(scanCtx, httpc, cfg, priv, leased.ScanID)
			if errors.Is(lErr, errNoWork) {
				log.Printf("scan %s: no more work units; exiting unit loop", leased.ScanID)
				return nil
			}
			if lErr != nil {
				// Cap-reached or transient: jitter and try again. The cap
				// case clears as siblings finish their units.
				log.Printf("scan %s: lease unit: %v (sleeping)", leased.ScanID, lErr)
				sleepWithJitter(scanCtx, cfg.LeasePoll)
				continue
			}
			unit = u
		}

		// Heartbeat in the background; cancelled when the unit's walk returns.
		hbCtx, hbCancel := context.WithCancel(scanCtx)
		go heartbeatUnitLoop(hbCtx, httpc, cfg, priv, leased.ScanID, unit.ID)

		walkErr := runUnitWalk(scanCtx, apiClient, conn, shallow, leased, root, unit, state, reporter, leased.Source.ExcludePatterns, extractor, extractFactory, cfg.ExtractWorkers)
		hbCancel()

		// Deliver the unit's terminal state, retrying on failure — see
		// deliverUnitTerminal. A dropped /complete leaves the unit
		// "running" until its lease expires and the API watchdog reaps
		// it, stalling scan finalization for ~2 min (v0.33.0).
		if walkErr != nil {
			log.Printf("scan %s unit %s: walk failed: %v", leased.ScanID, unit.ID, walkErr)
			deliverUnitTerminal("fail", leased.ScanID, unit.ID,
				func(ctx context.Context) error {
					return failUnit(ctx, httpc, cfg, priv, leased.ScanID, unit.ID, walkErr.Error())
				})
			continue
		}
		deliverUnitTerminal("complete", leased.ScanID, unit.ID,
			func(ctx context.Context) error {
				return completeUnit(ctx, httpc, cfg, priv, leased.ScanID, unit.ID)
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
//     shallow-walks the source root (subdirs only — files are emitted
//     later by the "" unit) and splits off a "root files" unit plus one
//     unit per top-level subdirectory, then returns (nil, nil) so the
//     caller's lease loop drains the freshly-enqueued work.
//   - probe gets 409 (cap) → units already exist but there is no slot
//     for us yet; return (nil, nil) and let the caller's lease loop
//     retry past the cap with backoff.
func ensureUnitsEnumerated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, root string,
	shallow connector.ShallowWalker, excludes []string,
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
		return nil, fmt.Errorf("probe lease: %w", err)
	}

	// 204 from probe → we're the first scanner; enumerate via the
	// connector's shallow walk. We discard the file emissions here —
	// they're handled by the "" unit when it gets leased. The point of
	// this call is just to discover the immediate subdirectory names.
	subdirs, err := shallow.WalkShallow(ctx, root, excludes, false,
		func(*models.EntryRecord) error { return nil })
	if err != nil {
		return nil, fmt.Errorf("enumerate root: %w", err)
	}

	paths := make([]string, 0, len(subdirs)+1)
	paths = append(paths, "")
	paths = append(paths, subdirs...)
	res, err := splitUnits(ctx, httpc, cfg, priv, scanID, nil, paths)
	if err != nil {
		return nil, err
	}
	log.Printf("scan %s: enumerated %d units (created=%d, skipped=%d)",
		scanID, len(paths), res.Created, res.Skipped)
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

// runUnitWalk dispatches a single leased unit to the right walker. The
// "" unit gets a shallow walk (root files only; subdirs already split
// off). Other units get a full recursive walk rooted at root + unit.path
// via the existing scanner.Run path so all the scanner.Run polish
// (incremental hashing, batching, observability) applies unchanged.
//
// Note: the per-unit scanner.Run will call conn.Connect again. For
// remote connectors that's a fresh handshake; we already paid one
// in runUnitCoordinated to keep the connection live, so the second
// Connect is fast (libraries reuse the underlying transport / TCP
// session for the lifetime of *Connector — they're idempotent).
func runUnitWalk(
	ctx context.Context,
	apiClient *client.Client,
	conn connector.Connector,
	shallow connector.ShallowWalker,
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
	if unit.Path == "" {
		return runRootFilesUnit(
			ctx, apiClient, shallow, reporter,
			leased.Source.ID, leased.ScanID,
			root, excludes,
			extractor, extractFactory, extractWorkers,
		)
	}
	subRoot := joinSubpath(leased.Source.Type, root, unit.Path)
	// v0.29.2 — adaptive batch sizing for unit runs too. Each unit
	// builds its own batcher rather than sharing one across units;
	// shared state would couple a slow SMB unit to a fast local one
	// and erase the per-unit signal AIMD wants to act on.
	batcher := newAdaptiveBatcher(leased.ScanID)
	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        leased.Source.ID,
		ScanID:          leased.ScanID,
		Root:            subRoot,
		BatchSize:       500,
		AdaptiveBatcher: batcher,
		Hash:            leased.ScanType == "full",
		ExcludePatterns: excludes,
		State:           state,
		// v0.31.6 — wire the shared Reporter so this unit's walk streams
		// its log lines (connecting / walk progress / per-file current
		// path) to /api/scans/{id}/log and on into the Live Log. Without
		// it scanner.Run falls back to local stdout and a multi-scanner
		// scan's Live Log shows nothing — stuck "waiting for output".
		Reporter: reporter,
		// v0.30.0 — content extraction for this unit's subtree.
		Extractor:               extractor,
		ExtractConnectorFactory: extractFactory,
		ExtractWorkers:          extractWorkers,
		// v0.31.5 — this is one unit of a unit-coordinated scan, not a
		// whole scan. Suppress the wire IsFinal flag so this unit's
		// last batch doesn't make the API complete (and stale-sweep)
		// the entire scan while sibling units are still running. The
		// scan is finalized by the work-unit /complete path. Consistent
		// with runRootFilesUnit, which already sends IsFinal=false.
		SuppressScanFinal: true,
	})
	_, err := s.Run(ctx)
	return err
}

// runRootFilesUnit walks JUST the root directory's immediate files
// (no subdirectories — those are their own units). Emits one batch
// directly to the api via apiClient.SendBatch. Skips the full
// scanner.Run scaffolding because there's no nested walk to reuse.
//
// Uses the connector's WalkShallow so this works uniformly for local,
// nfs, smb, and s3 sources.
func runRootFilesUnit(
	ctx context.Context, apiClient *client.Client,
	shallow connector.ShallowWalker,
	reporter *observe.Reporter,
	sourceID, scanID, root string, excludePatterns []string,
	extractor *extract.Extractor,
	extractFactory func() (connector.Connector, error),
	extractWorkers int,
) error {
	// v0.31.6 — stream a line to the Live Log so even a source whose
	// only unit is the root-files unit isn't stuck "waiting for output".
	reporter.LogSink().Info("root-files unit: scanning root-level files")
	var batch []models.EntryRecord
	_, err := shallow.WalkShallow(ctx, root, excludePatterns, false,
		func(entry *models.EntryRecord) error {
			batch = append(batch, *entry)
			return nil
		})
	if err != nil {
		return err
	}
	if len(batch) == 0 {
		reporter.LogSink().Info("root-files unit: no root-level files")
		return nil
	}
	reporter.LogSink().Info("root-files unit: %d root-level file(s)", len(batch))
	scanBatch := models.ScanBatch{
		SourceID: sourceID, ScanID: scanID,
		// IsFinal=false intentionally — on the unit-coordinated path
		// the api auto-finalizes the scan when the LAST work unit
		// (including the synthetic root-files unit) reaches /complete.
		// Setting IsFinal=true here would race the api's per-unit
		// finalization and double-trigger the post-scan rollup +
		// snapshot + webhook tasks. (Review notable — confirmed: api
		// path is canonical, not the batch flag.)
		// v0.31.5 — the non-root units enforce the same invariant via
		// scanner.Options.SuppressScanFinal; runRootFilesUnit sets it
		// directly here because it builds its batch without scanner.Run.
		Entries: batch, IsFinal: false,
	}
	resp, err := apiClient.SendBatch(ctx, scanBatch)
	if err != nil {
		return err
	}
	// v0.30.0 — extract text for the root-level files the API flagged
	// new/changed. Best-effort: failures are logged inside
	// DriveExtraction, never returned.
	if resp != nil && len(resp.ExtractCandidates) > 0 && extractor != nil && extractFactory != nil {
		extractConn, ecErr := extractFactory()
		if ecErr != nil {
			log.Printf("scan %s: root-files extraction skipped: %v", scanID, ecErr)
		} else {
			scanner.DriveExtraction(
				ctx, apiClient, extractor, extractConn, extractWorkers,
				sourceID, scanID, resp.ExtractCandidates,
				func(f string, a ...any) { log.Printf("scan "+scanID+": "+f, a...) },
			)
		}
	}
	return nil
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
