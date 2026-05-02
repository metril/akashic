package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/agent"
)

// runDiscover implements `akashic-scanner discover` — the
// scanner-initiated discovery flow used when no join token is
// available. The scanner generates a keypair, posts its public key +
// hostname to /api/scanners/discover, prints the pairing code on
// stderr for the operator to confirm, and long-polls until the
// discovery is approved/denied/expired or the local --timeout fires.
func runDiscover(args []string) {
	fs := flag.NewFlagSet("discover", flag.ExitOnError)
	apiURL := fs.String("api", "", "Akashic API base URL")
	pool := fs.String("pool", "default", "Suggested pool to display in the operator UI")
	keyPath := fs.String("key", "/secrets/scanner.key", "Where to write the freshly-generated private key")
	idPath := fs.String("id-file", "/secrets/scanner.id", "Where to write the assigned scanner UUID")
	timeout := fs.Duration("timeout", 15*time.Minute, "How long to wait for an admin decision")
	startAfter := fs.Bool("start-after", false, "Once approved, exec into `agent`")
	hostnameOverride := fs.String("hostname", "", "Override the hostname reported to the api")
	if err := fs.Parse(args); err != nil {
		log.Fatalf("discover flags: %v", err)
	}
	if *apiURL == "" {
		fs.Usage()
		log.Fatal("--api is required")
	}

	host := *hostnameOverride
	if host == "" {
		host, _ = os.Hostname()
	}

	priv, pub, err := agent.GenerateKeypair()
	if err != nil {
		log.Fatalf("generate keypair: %v", err)
	}
	if err := agent.WritePrivateKey(*keyPath, priv); err != nil {
		log.Fatalf("persist key: %v", err)
	}

	apiBase := strings.TrimRight(*apiURL, "/")
	discoveryID, pairing, expiresAt, err := postDiscover(context.Background(), apiBase, map[string]any{
		"public_key_pem": pub,
		"hostname":       host,
		"agent_version":  Version,
		"requested_pool": *pool,
	})
	if err != nil {
		_ = os.Remove(*keyPath)
		log.Fatalf("discover failed: %v", err)
	}

	fmt.Fprintln(os.Stderr, strings.Repeat("─", 60))
	fmt.Fprintf(os.Stderr, " Pending claim — pairing code:  %s\n", pairing)
	fmt.Fprintf(os.Stderr, " Approve in the Akashic UI:\n")
	fmt.Fprintf(os.Stderr, "   %s/settings/scanners#pending\n", apiBase)
	fmt.Fprintf(os.Stderr, " Expires at: %s\n", expiresAt.Format(time.RFC3339))
	fmt.Fprintln(os.Stderr, strings.Repeat("─", 60))

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	scannerID, scannerName, assignedPool := pollDiscover(ctx, apiBase, discoveryID)
	if scannerID == "" {
		// Non-zero exit; the polling helper already logged the reason
		// and chose the right exit code on its way to os.Exit().
		return
	}
	if err := agent.WriteScannerID(*idPath, scannerID); err != nil {
		log.Fatalf("persist scanner id: %v", err)
	}
	fmt.Fprintf(os.Stderr, "Approved as scanner %s (name=%s, pool=%s)\n", scannerID, scannerName, assignedPool)
	if *startAfter {
		execAgent(*apiURL, scannerID, *keyPath)
	}
}

func postDiscover(ctx context.Context, apiBase string, body map[string]any) (string, string, time.Time, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return "", "", time.Time{}, err
	}
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost,
		apiBase+"/api/scanners/discover",
		bytes.NewReader(buf),
	)
	if err != nil {
		return "", "", time.Time{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	cli := &http.Client{Timeout: 30 * time.Second}
	resp, err := cli.Do(req)
	if err != nil {
		return "", "", time.Time{}, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode/100 != 2 {
		// 404 = discovery disabled on the server; phrase the error so
		// the operator can find the toggle.
		if resp.StatusCode == 404 {
			return "", "", time.Time{}, fmt.Errorf(
				"discovery endpoint returned 404 — is `discovery_enabled` turned on in the api's Settings → Scanners pane?",
			)
		}
		return "", "", time.Time{}, fmt.Errorf("api returned %d: %s", resp.StatusCode, strings.TrimSpace(string(respBody)))
	}
	var out struct {
		DiscoveryID string    `json:"discovery_id"`
		PairingCode string    `json:"pairing_code"`
		ExpiresAt   time.Time `json:"expires_at"`
	}
	if err := json.Unmarshal(respBody, &out); err != nil {
		return "", "", time.Time{}, err
	}
	return out.DiscoveryID, out.PairingCode, out.ExpiresAt, nil
}

// pollDiscover long-polls /discover/{id} until the api returns a
// terminal status or the parent context (the user's --timeout)
// fires. Returns ("", "", "") on every non-success path AFTER
// terminating the process with an appropriate exit code, so the
// caller doesn't need to map states to exits itself.
//
// Exit codes:
//
//	0   success — caller writes the id file and optionally execs agent
//	64  denied  — admin rejected the discovery
//	65  expired — TTL elapsed before any decision
//	66  timeout — caller's --timeout fired before the api decided
//	1   network or unexpected error
func pollDiscover(ctx context.Context, apiBase, discoveryID string) (string, string, string) {
	cli := &http.Client{Timeout: 35 * time.Second} // > server long-poll window
	for {
		select {
		case <-ctx.Done():
			fmt.Fprintln(os.Stderr, "discovery timed out before an admin decided")
			os.Exit(66)
		default:
		}
		req, err := http.NewRequestWithContext(
			ctx, http.MethodGet,
			apiBase+"/api/scanners/discover/"+discoveryID, nil,
		)
		if err != nil {
			log.Fatalf("build poll request: %v", err)
		}
		resp, err := cli.Do(req)
		if err != nil {
			// Transient network error — back off briefly and retry.
			fmt.Fprintf(os.Stderr, "poll error (will retry): %v\n", err)
			select {
			case <-ctx.Done():
				os.Exit(66)
			case <-time.After(2 * time.Second):
			}
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode/100 != 2 {
			fmt.Fprintf(os.Stderr, "poll returned %d: %s\n", resp.StatusCode, strings.TrimSpace(string(body)))
			os.Exit(1)
		}
		var st struct {
			Status     string `json:"status"`
			ScannerID  string `json:"scanner_id"`
			Name       string `json:"name"`
			Pool       string `json:"pool"`
			DenyReason string `json:"deny_reason"`
		}
		if err := json.Unmarshal(body, &st); err != nil {
			log.Fatalf("decode poll response: %v", err)
		}
		switch st.Status {
		case "approved":
			return st.ScannerID, st.Name, st.Pool
		case "denied":
			fmt.Fprintf(os.Stderr, "admin denied discovery")
			if st.DenyReason != "" {
				fmt.Fprintf(os.Stderr, ": %s", st.DenyReason)
			}
			fmt.Fprintln(os.Stderr)
			os.Exit(64)
		case "expired":
			fmt.Fprintln(os.Stderr, "discovery expired before an admin decided")
			os.Exit(65)
		case "pending":
			// Loop back into the long-poll. The server holds open ~25s,
			// so this is mostly idle.
			continue
		default:
			fmt.Fprintf(os.Stderr, "unexpected discovery status: %q\n", st.Status)
			os.Exit(1)
		}
	}
}
