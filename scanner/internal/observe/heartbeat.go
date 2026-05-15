package observe

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
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

	// 409 is the API's "this scan ended — please stop" signal. We pull
	// the cancel-callback trigger exactly once; subsequent 409s (which
	// keep arriving until our process exits) are no-ops.
	//
	// v0.29.8 — the body now carries {status, reason, message}. Pre-fix
	// every 409 was logged as "scan cancelled by user" — wrong when
	// the API watchdog had reaped a stale scan or a sibling scanner
	// closed it cleanly. Decode and route the message accordingly.
	if resp.StatusCode == http.StatusConflict {
		r.logSink.Warn(decodeCancelMessage(resp.Body))
		r.signalCancel()
	}
}

// decodeCancelMessage parses the 409 body and produces an accurate
// exit-log line. Falls back to "scan cancelled by user; exiting" when
// the body is missing, malformed, or pre-v0.29.8 (legacy contract).
func decodeCancelMessage(body io.Reader) string {
	type detail struct {
		Detail struct {
			Status  string `json:"status"`
			Reason  string `json:"reason"`
			Message string `json:"message"`
		} `json:"detail"`
	}
	var d detail
	if err := json.NewDecoder(body).Decode(&d); err != nil {
		return "scan cancelled by user; exiting"
	}
	switch d.Detail.Reason {
	case "user", "":
		return "scan cancelled by user; exiting"
	case "watchdog":
		return "scan terminated by watchdog (stale heartbeat); exiting"
	case "completed":
		return "scan completed; exiting"
	default:
		return "scan ended (reason=" + d.Detail.Reason + "); exiting"
	}
}
