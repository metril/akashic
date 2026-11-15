// On-demand probe long-poll loop. Replaces the v0.5.7 polling loop —
// the API no longer enqueues continuous reachability_check rows.
//
// The agent calls GET /api/v1/scanners/{id}/probes/long-poll with a
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
	"log"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/probe"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

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
) (*apiclient.ProbeRequest, error) {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return nil, err
	}
	// The client's request timeout (60 s) covers the server's 30 s
	// long-poll plus a margin; a hung server can't pin this goroutine.
	resp, err := api.LongPollProbesWithResponse(ctx, cfg.ScannerID)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode() == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode() != http.StatusOK || resp.JSON200 == nil {
		return nil, httpError("long-poll", resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

// runOneProbe runs a probe in the background-bounded context and
// POSTs the report. Errors are logged but never propagate — the loop
// continues regardless so a single broken probe doesn't pin the agent.
func runOneProbe(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	req apiclient.ProbeRequest,
) {
	probeCtx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()

	start := time.Now()
	log.Printf("probe %s: started source=%s type=%s",
		req.RequestId, req.SourceId, req.SourceType)

	result := probe.Run(probeCtx, req.SourceType, req.ConnectionConfig)
	elapsed := time.Since(start)
	if result.OK {
		log.Printf("probe %s: ok in %s", req.RequestId, elapsed.Round(time.Millisecond))
	} else {
		log.Printf("probe %s: failed step=%s error=%q in %s",
			req.RequestId, result.Step, result.Error,
			elapsed.Round(time.Millisecond))
	}

	body := apiclient.ProbeReport{
		Ok:       result.OK,
		Step:     wire.Str(result.Step),
		Error:    wire.Str(result.Error),
		SourceId: req.SourceId,
	}
	if err := postProbeReport(ctx, httpc, cfg, priv, req.RequestId, body); err != nil {
		log.Printf("probe %s: report POST failed source=%s: %v",
			req.RequestId, req.SourceId, err)
	}
}

func postProbeReport(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
	requestID uuid.UUID, report apiclient.ProbeReport,
) error {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return err
	}
	resp, err := api.ReportProbeResultWithResponse(ctx, cfg.ScannerID, requestID, report)
	if err != nil {
		return err
	}
	if resp.StatusCode() != http.StatusNoContent && resp.StatusCode() != http.StatusOK {
		return httpError("report", resp.StatusCode(), resp.Body)
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
