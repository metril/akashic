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
	// (path "") plus one unit per top-level subdirectory. Subsequent
	// scanners arriving here will already see units in the queue and
	// skip the enumeration step.
	if err := ensureUnitsEnumerated(scanCtx, httpc, cfg, priv, leased.ScanID, root, shallow, leased.Source.ExcludePatterns); err != nil {
		return fmt.Errorf("enumerate units: %w", err)
	}

	// Lease loop. Stops when /work/lease returns 204 (no work for me).
	for {
		if err := scanCtx.Err(); err != nil {
			return err
		}
		unit, err := leaseUnit(scanCtx, httpc, cfg, priv, leased.ScanID)
		if errors.Is(err, errNoWork) {
			log.Printf("scan %s: no more work units; exiting unit loop", leased.ScanID)
			return nil
		}
		if err != nil {
			// Cap-reached or transient: jitter and try again. The cap
			// case clears as siblings finish their units.
			log.Printf("scan %s: lease unit: %v (sleeping)", leased.ScanID, err)
			sleepWithJitter(scanCtx, cfg.LeasePoll)
			continue
		}

		// Heartbeat in the background; cancelled when the unit's walk returns.
		hbCtx, hbCancel := context.WithCancel(scanCtx)
		go heartbeatUnitLoop(hbCtx, httpc, cfg, priv, leased.ScanID, unit.ID)

		walkErr := runUnitWalk(scanCtx, apiClient, conn, shallow, leased, root, unit, state, leased.Source.ExcludePatterns)
		hbCancel()

		// Terminal-status delivery uses a fresh, short-lived context
		// rooted in Background so a cancelled scanCtx (Stop pressed,
		// SIGTERM, heartbeat 409) doesn't prevent us from telling the
		// api the unit is done. Without this the unit stays "leased"
		// and only releases when the api-side lease TTL expires
		// (review S-C1).
		termCtx, termCancel := context.WithTimeout(context.Background(), 5*time.Second)
		if walkErr != nil {
			log.Printf("scan %s unit %s: walk failed: %v", leased.ScanID, unit.ID, walkErr)
			_ = failUnit(termCtx, httpc, cfg, priv, leased.ScanID, unit.ID, walkErr.Error())
			termCancel()
			continue
		}
		if err := completeUnit(termCtx, httpc, cfg, priv, leased.ScanID, unit.ID); err != nil {
			log.Printf("scan %s unit %s: complete failed: %v", leased.ScanID, unit.ID, err)
		}
		termCancel()
	}
}

// ensureUnitsEnumerated calls /work/lease once to probe for existing
// units. On 204 (no units) it lists the source root via the
// connector's WalkShallow (subdirs only — files are emitted later by
// the "" unit), splits off subdirs + a root-files unit, then returns
// so the caller's lease loop picks up the freshly-enqueued work.
//
// A leased unit returned here is immediately RELEASED (we don't act on
// it) by NOT calling complete — the lease will expire after 60s and
// another scanner can pick it up. This is deliberate: the enumerator
// scanner doesn't want to monopolize the first leased unit; it just
// wants to confirm whether enumeration has happened.
func ensureUnitsEnumerated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, root string,
	shallow connector.ShallowWalker, excludes []string,
) error {
	probe, err := leaseUnit(ctx, httpc, cfg, priv, scanID)
	if err == nil && probe != nil {
		log.Printf(
			"scan %s: enumeration already done by a sibling; "+
				"releasing probe unit %s", scanID, probe.ID,
		)
		return nil
	}
	if err != nil && !errors.Is(err, errNoWork) {
		return fmt.Errorf("probe lease: %w", err)
	}

	// 204 from probe → we're the first scanner; enumerate via the
	// connector's shallow walk. We discard the file emissions here —
	// they're handled by the "" unit when it gets leased. The point of
	// this call is just to discover the immediate subdirectory names.
	subdirs, err := shallow.WalkShallow(ctx, root, excludes, false,
		func(*models.EntryRecord) error { return nil })
	if err != nil {
		return fmt.Errorf("enumerate root: %w", err)
	}

	paths := make([]string, 0, len(subdirs)+1)
	paths = append(paths, "")
	paths = append(paths, subdirs...)
	res, err := splitUnits(ctx, httpc, cfg, priv, scanID, nil, paths)
	if err != nil {
		return err
	}
	log.Printf("scan %s: enumerated %d units (created=%d, skipped=%d)",
		scanID, len(paths), res.Created, res.Skipped)
	return nil
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
	excludes []string,
) error {
	if unit.Path == "" {
		return runRootFilesUnit(
			ctx, apiClient, shallow,
			leased.Source.ID, leased.ScanID,
			root, excludes,
		)
	}
	subRoot := joinSubpath(leased.Source.Type, root, unit.Path)
	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        leased.Source.ID,
		ScanID:          leased.ScanID,
		Root:            subRoot,
		BatchSize:       1000,
		Hash:            leased.ScanType == "full",
		ExcludePatterns: excludes,
		State:           state,
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
	sourceID, scanID, root string, excludePatterns []string,
) error {
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
		return nil
	}
	scanBatch := models.ScanBatch{
		SourceID: sourceID, ScanID: scanID,
		// IsFinal=false intentionally — on the unit-coordinated path
		// the api auto-finalizes the scan when the LAST work unit
		// (including the synthetic root-files unit) reaches /complete.
		// Setting IsFinal=true here would race the api's per-unit
		// finalization and double-trigger the post-scan rollup +
		// snapshot + webhook tasks. (Review notable — confirmed: api
		// path is canonical, not the batch flag.)
		Entries: batch, IsFinal: false,
	}
	return apiClient.SendBatch(ctx, scanBatch)
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
