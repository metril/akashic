// Unit-coordinated scan runner (Phase 2 of v0.5.x parallel scanning).
//
// When MaxParallelScanners > 1 on a leased scan AND the connector type
// is local/nfs, the agent enters a lease loop over scan_work_units
// instead of running a single monolithic walk. This lets sibling
// scanners (within the same pool) cooperate on one source — each
// claims a different top-level subtree.
//
// The walk model is intentionally simple:
//   - The first scanner to lease /work hits 204 (no units yet); it
//     enumerates the source root, splits off the immediate
//     subdirectories as units, plus a special "" unit for
//     root-level files, then re-leases.
//   - The "" unit performs a SHALLOW walk (root-level files only;
//     subdirectories already split off).
//   - Every other unit performs a full recursive walk of the source
//     root + unit.path subtree using the existing scanner.Run path.
//
// Unsupported connectors (ssh/smb/s3) fall back to the legacy single-
// walker path — they would need per-connector "list immediate
// children" support to play in the unit model. Tracked for follow-up.
package agent

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
	"github.com/akashic-project/akashic/scanner/internal/walker"
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
	root := stringFromConfig(leased.Source.ConnectionConfig, "path", "")
	apiClient := client.New(cfg.APIBase, leased.APIJWT)

	state := observe.NewState()
	reporter := observe.New(cfg.APIBase, leased.APIJWT, leased.ScanID, state)
	scanCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	reporter.SetUserCancel(cancel)
	reporter.Start(scanCtx)
	defer reporter.Stop()

	// Up-front enumeration: if no units exist yet for this scan, the
	// first scanner to arrive splits the source into a "root files" unit
	// (path "") plus one unit per top-level subdirectory. Subsequent
	// scanners arriving here will already see units in the queue and
	// skip the enumeration step.
	if err := ensureUnitsEnumerated(ctx, httpc, cfg, priv, leased.ScanID, root); err != nil {
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

		walkErr := runUnitWalk(scanCtx, apiClient, conn, leased, root, unit, state)
		hbCancel()

		if walkErr != nil {
			log.Printf("scan %s unit %s: walk failed: %v", leased.ScanID, unit.ID, walkErr)
			_ = failUnit(scanCtx, httpc, cfg, priv, leased.ScanID, unit.ID, walkErr.Error())
			continue
		}
		if err := completeUnit(scanCtx, httpc, cfg, priv, leased.ScanID, unit.ID); err != nil {
			log.Printf("scan %s unit %s: complete failed: %v", leased.ScanID, unit.ID, err)
		}
	}
}

// ensureUnitsEnumerated calls /work/lease once to probe for existing
// units. On 204 (no units) it lists the source root, splits off
// subdirs + a root-files unit, then returns so the caller's lease
// loop picks up the freshly-enqueued work.
//
// A leased unit returned here is immediately RELEASED (we don't act on
// it) by NOT calling complete — the lease will expire after 60s and
// another scanner can pick it up. This is deliberate: the enumerator
// scanner doesn't want to monopolize the first leased unit; it just
// wants to confirm whether enumeration has happened. If we DID return
// the leased unit to the caller, the caller would have to handle the
// "this is my first unit, also enumerate" case — messier than this.
func ensureUnitsEnumerated(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, root string,
) error {
	probe, err := leaseUnit(ctx, httpc, cfg, priv, scanID)
	if err == nil && probe != nil {
		// A unit was leased to us; another scanner already enumerated.
		// We let the lease expire (60s) so a sibling can pick it up,
		// rather than holding it idly. Cheap.
		log.Printf(
			"scan %s: enumeration already done by a sibling; "+
				"releasing probe unit %s", scanID, probe.ID,
		)
		return nil
	}
	if err != nil && !errors.Is(err, errNoWork) {
		return fmt.Errorf("probe lease: %w", err)
	}

	// 204 from probe → we're the first scanner; enumerate.
	subdirs, err := listImmediateSubdirs(root)
	if err != nil {
		return fmt.Errorf("list root: %w", err)
	}
	// "" = root-level files unit. Split everything in one call so
	// concurrent scanners arriving here also see the populated queue.
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

// listImmediateSubdirs returns the names of immediate subdirectories
// under `root`. Files are ignored (they're handled by the "" unit).
// Used by the enumerator to fan top-level subtrees out as work units.
func listImmediateSubdirs(root string) ([]string, error) {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, e.Name())
		}
	}
	return out, nil
}

// runUnitWalk dispatches a single leased unit to the right walker. The
// "" unit gets a shallow walk (root files only; subdirs already split
// off). Other units get a full recursive walk rooted at root + unit.path
// via the existing scanner.Run path so all the scanner.Run polish
// (incremental hashing, batching, observability) applies unchanged.
func runUnitWalk(
	ctx context.Context,
	apiClient *client.Client,
	conn connector.Connector,
	leased *leasedScan,
	root string,
	unit *workUnit,
	state *observe.State,
) error {
	if unit.Path == "" {
		return runRootFilesUnit(
			ctx, apiClient, leased.Source.ID, leased.ScanID,
			root, leased.Source.ExcludePatterns,
		)
	}
	subRoot := filepath.Join(root, unit.Path)
	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        leased.Source.ID,
		ScanID:          leased.ScanID,
		Root:            subRoot,
		BatchSize:       1000,
		Hash:            leased.ScanType == "full",
		ExcludePatterns: leased.Source.ExcludePatterns,
		State:           state,
	})
	_, err := s.Run(ctx)
	return err
}

// runRootFilesUnit walks JUST the root directory's immediate files
// (no subdirectories — those are their own units). Emits one batch
// directly to the api via apiClient.SendBatch. Skips the full
// scanner.Run scaffolding because there's no nested walk to reuse.
func runRootFilesUnit(
	ctx context.Context, apiClient *client.Client,
	sourceID, scanID, root string, excludePatterns []string,
) error {
	var batch []models.EntryRecord
	res, err := walker.WalkShallow(ctx, root, excludePatterns, false, func(entry *models.EntryRecord) error {
		batch = append(batch, *entry)
		return nil
	})
	if err != nil {
		return err
	}
	_ = res // SubdirNames already enqueued by the enumerator; we ignore them here
	scanBatch := models.ScanBatch{
		SourceID: sourceID, ScanID: scanID,
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
