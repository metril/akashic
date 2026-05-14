// On-demand probe long-poll loop. Replaces the v0.5.7 polling loop —
// the API no longer enqueues continuous reachability_check rows.
//
// The agent calls GET /api/scanners/{id}/probes/long-poll with a
// scanner JWT; the API holds the connection open for ~30 s waiting
// for a probe to be published on the agent's per-id Redis channel.
// On 200 the agent runs the probe via internal/probe, then POSTs
// the result to /probes/{request_id}/report. On 204 (timeout) the
// loop reconnects immediately. On error it backs off 5 s.
//
// Concurrency: one probe at a time, sequenced through the loop. Probes
// are user-triggered configuration actions — fan-out is unnecessary
// and would just complicate failure handling.
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

type probeRequest struct {
	RequestID        string         `json:"request_id"`
	SourceID         string         `json:"source_id"`
	SourceType       string         `json:"source_type"`
	ConnectionConfig map[string]any `json:"connection_config"`
}

type probeReport struct {
	OK       bool   `json:"ok"`
	Step     string `json:"step,omitempty"`
	Error    string `json:"error,omitempty"`
	SourceID string `json:"source_id"`
}

// reachabilityLoop is started in Run alongside the scan-lease loop.
// Each iteration: long-poll for one probe, run it, post the result.
func reachabilityLoop(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) {
	for {
		if ctx.Err() != nil {
			return
		}
		req, err := waitForProbe(ctx, httpc, cfg, priv)
		if err != nil {
			log.Printf("probes long-poll: %v", err)
			sleepFor(ctx, 5*time.Second)
			continue
		}
		if req == nil {
			// 204 timeout — reconnect immediately. The scanner JWT is
			// minted fresh per call so JTI replay protection still
			// gives every long-poll its own auth identity.
			continue
		}
		runOneProbe(ctx, httpc, cfg, priv, *req)
	}
}

// waitForProbe blocks on the long-poll endpoint until a probe is
// delivered or the server times out.
func waitForProbe(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) (*probeRequest, error) {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return nil, err
	}
	url := fmt.Sprintf("%s/api/scanners/%s/probes/long-poll",
		cfg.APIBase, cfg.ScannerID)

	// Use a per-request context with a generous client-side timeout so
	// a hung server doesn't pin this goroutine forever. The default
	// client Timeout (60 s) covers the server's 30 s long-poll plus a
	// margin; we leave it alone here.
	httpReq, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", auth)
	resp, err := httpc.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("long-poll HTTP %d: %s", resp.StatusCode, string(raw))
	}
	var pr probeRequest
	if err := json.NewDecoder(resp.Body).Decode(&pr); err != nil {
		return nil, fmt.Errorf("decode probe request: %w", err)
	}
	return &pr, nil
}

// runOneProbe runs a probe in the background-bounded context and
// POSTs the report. Errors are logged but never propagate — the loop
// continues regardless so a single broken probe doesn't pin the agent.
func runOneProbe(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	req probeRequest,
) {
	probeCtx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()

	start := time.Now()
	log.Printf("probe %s: started source=%s type=%s",
		req.RequestID, req.SourceID, req.SourceType)

	result := probe.Run(probeCtx, req.SourceType, req.ConnectionConfig)
	elapsed := time.Since(start)
	if result.OK {
		log.Printf("probe %s: ok in %s", req.RequestID, elapsed.Round(time.Millisecond))
	} else {
		log.Printf("probe %s: failed step=%s error=%q in %s",
			req.RequestID, result.Step, result.Error,
			elapsed.Round(time.Millisecond))
	}

	body := probeReport{
		OK:       result.OK,
		Step:     result.Step,
		Error:    result.Error,
		SourceID: req.SourceID,
	}
	if err := postProbeReport(ctx, httpc, cfg, priv, req.RequestID, body); err != nil {
		log.Printf("probe %s: report POST failed source=%s: %v",
			req.RequestID, req.SourceID, err)
	}
}

func postProbeReport(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	requestID string, report probeReport,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scanners/%s/probes/%s/report",
		cfg.APIBase, cfg.ScannerID, requestID)
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

// sleepFor waits d, or returns early on ctx cancel.
func sleepFor(ctx context.Context, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}
