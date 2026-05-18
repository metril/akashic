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
			if terminal := r.postHeartbeat(ctx); terminal {
				// The API answered 409 — the scan has ended. Stop here:
				// pinging a dead scan every second is pointless, and
				// looping back to the ctx.Done() branch would post
				// (and log the exit line) a second time.
				return
			}
		}
	}
}

// postHeartbeat POSTs the current counter snapshot. It returns true when
// the API answered 409 — the scan has ended and the caller should stop
// heartbeating.
func (r *Reporter) postHeartbeat(ctx context.Context) (terminal bool) {
	body, err := json.Marshal(r.state.snapshot())
	if err != nil {
		// Marshal of our own struct shouldn't fail; if it does, log via
		// the structured logger so it's visible in the UI rather than
		// silently swallowed.
		r.logSink.Warn("heartbeat marshal failed: %v", err)
		return false
	}
	url := r.apiURL + "/api/scans/" + r.scanID + "/heartbeat"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return false
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+r.apiKey)

	resp, err := r.httpClient.Do(req)
	if err != nil {
		// Network blip — let the next tick retry. Don't flood the log if
		// the API is down for an extended period.
		return false
	}
	defer resp.Body.Close()

	// 409 is the API's "this scan ended — please stop" signal.
	//
	// v0.29.8 — the body carries {status, reason, message}. v0.33.0 —
	// route the level too: a normal completion is INFO, a cancellation
	// or watchdog reap is WARN. The caller stops the loop on `true`, so
	// the exit line is logged exactly once (pre-fix every 1 s tick that
	// landed after the scan ended re-logged it).
	if resp.StatusCode == http.StatusConflict {
		msg, completed := decodeCancelMessage(resp.Body)
		if completed {
			r.logSink.Info(msg)
		} else {
			r.logSink.Warn(msg)
		}
		r.signalCancel()
		return true
	}
	return false
}

// decodeCancelMessage parses the 409 body into an accurate exit-log line
// and whether the scan ended by normal completion (vs. a cancellation or
// watchdog reap — which drives the log level). Falls back to a user-cancel
// line when the body is missing, malformed, or pre-v0.29.8 (legacy
// contract).
func decodeCancelMessage(body io.Reader) (msg string, completed bool) {
	type detail struct {
		Detail struct {
			Status  string `json:"status"`
			Reason  string `json:"reason"`
			Message string `json:"message"`
		} `json:"detail"`
	}
	var d detail
	if err := json.NewDecoder(body).Decode(&d); err != nil {
		return "scan cancelled by user; exiting", false
	}
	switch d.Detail.Reason {
	case "user", "":
		return "scan cancelled by user; exiting", false
	case "watchdog":
		return "scan terminated by watchdog (stale heartbeat); exiting", false
	case "completed":
		return "scan completed; exiting", true
	default:
		return "scan ended (reason=" + d.Detail.Reason + "); exiting", false
	}
}
