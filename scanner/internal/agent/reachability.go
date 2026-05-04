// Reachability poll loop. Runs alongside the scan-lease loop, claims
// reachability_check rows from /api/scanners/{id}/reachability/poll,
// runs an in-process probe via internal/probe, and reports the result.
//
// Independent of the scan poll cadence — reachability runs every 15 s
// (jittered ±20%) so a 200-source install gets full sweeps faster than
// the api-side enqueue interval and no row sits pending across more
// than ~one cycle.
package agent

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/probe"
)

const reachabilityPollEvery = 15 * time.Second

type reachabilityClaim struct {
	ID               string         `json:"id"`
	SourceID         string         `json:"source_id"`
	SourceType       string         `json:"source_type"`
	ConnectionConfig map[string]any `json:"connection_config"`
}

type reachabilityPollResponse struct {
	Checks []reachabilityClaim `json:"checks"`
}

type reachabilityReport struct {
	OK    bool   `json:"ok"`
	Step  string `json:"step,omitempty"`
	Error string `json:"error,omitempty"`
}

// reachabilityLoop is started in Run alongside the scan-lease loop.
// Each tick: poll for claims, probe each one, report result. Errors
// don't stop the loop — they log and back off.
func reachabilityLoop(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) {
	for {
		if ctx.Err() != nil {
			return
		}
		claims, err := pollReachabilityChecks(ctx, httpc, cfg, priv)
		if err != nil {
			log.Printf("reachability poll: %v", err)
			sleepWithJitter(ctx, reachabilityPollEvery)
			continue
		}
		if len(claims) == 0 {
			sleepWithJitter(ctx, reachabilityPollEvery)
			continue
		}
		for _, claim := range claims {
			if ctx.Err() != nil {
				return
			}
			runOneReachabilityProbe(ctx, httpc, cfg, priv, claim)
		}
		// After draining a batch, immediately poll again — the api
		// might have more leasable rows queued, no need to wait the
		// full poll interval.
	}
}

func pollReachabilityChecks(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) ([]reachabilityClaim, error) {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return nil, err
	}
	url := fmt.Sprintf("%s/api/scanners/%s/reachability/poll", cfg.APIBase, cfg.ScannerID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, struct{}{})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("reachability/poll HTTP %d: %s", resp.StatusCode, string(raw))
	}
	var pr reachabilityPollResponse
	if err := json.NewDecoder(resp.Body).Decode(&pr); err != nil {
		return nil, fmt.Errorf("decode poll response: %w", err)
	}
	return pr.Checks, nil
}

func runOneReachabilityProbe(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	claim reachabilityClaim,
) {
	// Each probe gets its own bounded context — a slow SSH / S3 probe
	// can't stall the whole loop. The api lease is 30 s; this matches.
	probeCtx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()

	result := probe.Run(probeCtx, claim.SourceType, claim.ConnectionConfig)

	body := reachabilityReport{
		OK:    result.OK,
		Step:  result.Step,
		Error: result.Error,
	}
	if err := reportReachabilityResult(ctx, httpc, cfg, priv, claim.ID, body); err != nil {
		log.Printf("reachability report (check=%s source=%s): %v",
			claim.ID, claim.SourceID, err)
	}
}

func reportReachabilityResult(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	checkID string, report reachabilityReport,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scanners/%s/reachability/%s/report",
		cfg.APIBase, cfg.ScannerID, checkID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, report)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent && resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("HTTP %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}
