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

// TestReachabilityLoop_LongPollDeliversProbeAndReports stands up a
// fake API that returns one local-source probe on the first long-
// poll, then 204s subsequent calls. Asserts the agent ran the probe
// and posted a report carrying the request_id + source_id back.
func TestReachabilityLoop_LongPollDeliversProbeAndReports(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}

	var pollCount int32
	var reportedReqID string
	var reportedSrcID string
	var reportedOK bool
	reported := make(chan struct{}, 1)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/scanners/scan-1/probes/long-poll", func(w http.ResponseWriter, r *http.Request) {
		count := atomic.AddInt32(&pollCount, 1)
		if count == 1 {
			req := probeRequest{
				RequestID:  "req-1",
				SourceID:   "src-1",
				SourceType: "local",
				ConnectionConfig: map[string]any{
					"root_path": t.TempDir(),
				},
			}
			_ = json.NewEncoder(w).Encode(req)
			return
		}
		// Subsequent long-polls timeout (204) so the loop idles.
		w.WriteHeader(http.StatusNoContent)
	})
	mux.HandleFunc("/api/scanners/scan-1/probes/", func(w http.ResponseWriter, r *http.Request) {
		// Path: /api/scanners/scan-1/probes/{request_id}/report
		// Split → ["", "api", "scanners", "scan-1", "probes", "{req}", "report"]
		parts := strings.Split(r.URL.Path, "/")
		if len(parts) < 7 || parts[6] != "report" {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		reportedReqID = parts[5]
		var rep probeReport
		_ = json.NewDecoder(r.Body).Decode(&rep)
		reportedSrcID = rep.SourceID
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
		t.Fatal("agent did not report a probe result within 5s")
	}

	if reportedReqID != "req-1" {
		t.Errorf("reported request id = %q, want req-1", reportedReqID)
	}
	if reportedSrcID != "src-1" {
		t.Errorf("reported source id = %q, want src-1", reportedSrcID)
	}
	if !reportedOK {
		t.Errorf("reported ok=false, want true (local probe against TempDir should succeed)")
	}
}

// TestReachabilityLoop_HandlesEmptyLongPoll verifies the loop doesn't
// spin or panic when the api returns 204 No Content.
func TestReachabilityLoop_HandlesEmptyLongPoll(t *testing.T) {
	_, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer srv.Close()

	httpc := &http.Client{Timeout: 5 * time.Second}
	cfg := Config{APIBase: srv.URL, ScannerID: "scan-1"}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	reachabilityLoop(ctx, httpc, cfg, priv)
	// No assertion — the test just needs to return promptly via ctx.Done.
}
