package observe

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"time"
)

// heartbeatInterval is fixed at 1 s. The API watchdog's freshness window
// is 60 s, leaving ~60 chances to recover a missed heartbeat before a
// scan gets killed for being stale.
const heartbeatInterval = 1 * time.Second

func (r *Reporter) runHeartbeat(ctx context.Context) {
	t := time.NewTicker(heartbeatInterval)
	defer t.Stop()

	for {
		select {
		case <-ctx.Done():
			// Final heartbeat on shutdown so the API sees the latest
			// counter values without waiting another tick. Fresh context
			// with a short deadline — the parent is already cancelled.
			finalCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			r.postHeartbeat(finalCtx)
			cancel()
			return
		case <-t.C:
			r.postHeartbeat(ctx)
		}
	}
}

func (r *Reporter) postHeartbeat(ctx context.Context) {
	body, err := json.Marshal(r.state.snapshot())
	if err != nil {
		// Marshal of our own struct shouldn't fail; if it does, log via
		// the structured logger so it's visible in the UI rather than
		// silently swallowed.
		r.logSink.Warn("heartbeat marshal failed: %v", err)
		return
	}
	url := r.apiURL + "/api/scans/" + r.scanID + "/heartbeat"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.httpClient.Do(req)
	if err != nil {
		// Network blip — log once and let the next tick retry. Don't
		// flood the log if the API is down for an extended period.
		return
	}
	defer resp.Body.Close()

	// 409 is the API's "this scan was cancelled — please stop"
	// signal. We pull the trigger on the cancel-callback exactly once;
	// subsequent 409s (which will keep arriving until our process
	// exits) are no-ops.
	if resp.StatusCode == http.StatusConflict {
		r.logSink.Warn("scan cancelled by user; exiting")
		r.signalCancel()
	}
}
