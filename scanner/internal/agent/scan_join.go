// Push-based scan-join loop (v0.29.0).
//
// Pre-v0.29.0 multi-scanner cooperation never worked: scanner B
// polling /api/v1/scans/lease was filtered out by assigned_scanner_id IS
// NULL once scanner A claimed the scan, and had no other channel to
// discover an in-flight cooperative scan. This loop is the channel:
// long-poll GET /api/v1/scanners/{id}/scans/long-poll, receive a join
// payload (same shape as /api/v1/scans/lease), dispatch to
// runUnitCoordinated — reusing the existing unit-lease + walk path.
//
// One join at a time per agent. The runUnitCoordinated call blocks
// until the scan is drained from this agent's POV (its leased units
// all finish); only then do we re-enter the long-poll. That matches
// the existing reachabilityLoop / lease-loop ergonomics — the agent
// commits to one scan's work before reaching for the next thing.
package agent

import (
	"context"
	"crypto/ed25519"
	"log"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// scanJoinLoop is started in Run alongside the heartbeat,
// reachability, and lease loops.
func scanJoinLoop(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) {
	// v0.29.5 — emit a heartbeat-style log line every Nth consecutive
	// 204 timeout so `docker compose logs scanner-2 | grep scan_join`
	// reveals "this loop is alive and waiting" — useful when the user
	// is debugging a re-scan where they expect the second scanner to
	// join but nothing seems to happen. N=10 keeps the cadence to
	// once every ~5 minutes (10 × 30s long-poll) on an idle agent.
	const idleHeartbeatEveryN = 10
	idleCount := 0

	for {
		if ctx.Err() != nil {
			return
		}
		leased, err := waitForJoin(ctx, httpc, cfg, priv)
		if err != nil {
			log.Printf("scans long-poll: %v", err)
			sleepFor(ctx, 5*time.Second)
			continue
		}
		if leased == nil {
			// 204 timeout — reconnect immediately. Per-call MintJWT
			// gives every long-poll a fresh JTI so replay protection
			// stays intact.
			idleCount++
			if idleCount%idleHeartbeatEveryN == 0 {
				log.Printf("scan_join: %d consecutive 204 timeouts — loop alive, no work pending",
					idleCount)
			}
			continue
		}
		idleCount = 0
		// We joined a cooperative scan. Run it through the same
		// unit-coordinated path the original lease holder uses.
		log.Printf("scan %s: joining via scan_join channel source=%s type=%s",
			leased.ScanId, leased.Source.Id, leased.Source.Type)
		if err := runUnitCoordinated(ctx, httpc, cfg, priv, leased); err != nil {
			log.Printf("joined scan %s failed: %v", leased.ScanId, err)
		}
	}
}

// waitForJoin blocks on the scans long-poll endpoint until a join
// payload arrives or the server times out (204).
func waitForJoin(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) (*apiclient.LeasedScan, error) {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return nil, err
	}
	resp, err := api.LongPollScanJoinsWithResponse(ctx, cfg.ScannerID)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode() == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode() != http.StatusOK || resp.JSON200 == nil {
		return nil, httpError("scans long-poll", resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}
