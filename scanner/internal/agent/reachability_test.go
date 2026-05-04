package agent

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// TestReachabilityLoop_PollsAndReportsLocalProbe stands up a fake api
// returning one local-source check, runs the loop for one tick, and
// asserts the agent posted a report for that check id.
func TestReachabilityLoop_PollsAndReportsLocalProbe(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}

	var pollCount int32
	var reportedCheckID string
	var reportedOK bool
	reported := make(chan struct{}, 1)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/scanners/scan-1/reachability/poll", func(w http.ResponseWriter, r *http.Request) {
		count := atomic.AddInt32(&pollCount, 1)
		if count == 1 {
			// First poll: hand the agent a local-source claim that
			// points at a path that exists (probe should succeed).
			body := reachabilityPollResponse{
				Checks: []reachabilityClaim{{
					ID:         "check-1",
					SourceID:   "src-1",
					SourceType: "local",
					ConnectionConfig: map[string]any{
						"root_path": t.TempDir(),
					},
				}},
			}
			_ = json.NewEncoder(w).Encode(body)
			return
		}
		// Subsequent polls return empty so the loop idles.
		_ = json.NewEncoder(w).Encode(reachabilityPollResponse{})
	})
	mux.HandleFunc("/api/scanners/scan-1/reachability/", func(w http.ResponseWriter, r *http.Request) {
		// Path: /api/scanners/scan-1/reachability/{check_id}/report
		// Split → ["", "api", "scanners", "scan-1", "reachability", "{check_id}", "report"]
		parts := strings.Split(r.URL.Path, "/")
		if len(parts) < 7 || parts[6] != "report" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		checkID := parts[5]
		var rep reachabilityReport
		_ = json.NewDecoder(r.Body).Decode(&rep)
		reportedCheckID = checkID
		reportedOK = rep.OK
		w.WriteHeader(http.StatusNoContent)
		select {
		case reported <- struct{}{}:
		default:
		}
	})

	srv := httptest.NewServer(mux)
	defer srv.Close()

	httpc := &http.Client{Timeout: 5 * time.Second}
	cfg := Config{APIBase: srv.URL, ScannerID: "scan-1"}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	go reachabilityLoop(ctx, httpc, cfg, priv)

	select {
	case <-reported:
	case <-time.After(5 * time.Second):
		t.Fatal("agent did not report a reachability result within 5s")
	}

	if reportedCheckID != "check-1" {
		t.Errorf("reported check id = %q, want check-1", reportedCheckID)
	}
	if !reportedOK {
		t.Errorf("reported ok=false, want true (local probe against TempDir should succeed)")
	}
}

// TestReachabilityLoop_HandlesEmptyPoll just verifies the loop doesn't
// spin or panic when the api returns no checks.
func TestReachabilityLoop_HandlesEmptyPoll(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(w).Encode(reachabilityPollResponse{})
	}))
	defer srv.Close()

	httpc := &http.Client{Timeout: 5 * time.Second}
	cfg := Config{APIBase: srv.URL, ScannerID: "scan-1"}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	reachabilityLoop(ctx, httpc, cfg, priv)
	// No assertion — the test just needs to return promptly.
}
