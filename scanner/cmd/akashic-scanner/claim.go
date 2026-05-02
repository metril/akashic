package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"syscall"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/agent"
)

// runClaim implements `akashic-scanner claim` — the self-registration
// path for a scanner host that's been handed a join token. Generates
// its own keypair, posts (token, public_key_pem) to the api, and on
// success persists the private key + scanner id locally. Optionally
// chains into the agent loop with --start-after.
func runClaim(args []string) {
	fs := flag.NewFlagSet("claim", flag.ExitOnError)
	apiURL := fs.String("api", "", "Akashic API base URL (e.g. https://api.example.com)")
	token := fs.String("token", "", "Join token (akcl_…) issued by the api admin")
	keyPath := fs.String("key", "/secrets/scanner.key", "Where to write the freshly-generated private key")
	idPath := fs.String("id-file", "/secrets/scanner.id", "Where to write the assigned scanner UUID")
	startAfter := fs.Bool("start-after", false, "After a successful claim, exec into `agent` with the new credentials")
	hostnameOverride := fs.String("hostname", "", "Override the hostname reported to the api (default: os.Hostname)")
	if err := fs.Parse(args); err != nil {
		log.Fatalf("claim flags: %v", err)
	}
	if *apiURL == "" || *token == "" {
		fs.Usage()
		log.Fatal("--api and --token are required")
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

	body := map[string]any{
		"token":          *token,
		"public_key_pem": pub,
		"hostname":       host,
		"agent_version":  Version,
	}
	// Same backoff strategy as discover — survive cold starts where
	// the api isn't yet accepting connections, network blips during
	// a restart, etc. 5-minute total budget; claims are usually
	// one-shot so a longer retry window doesn't help anyone.
	postCtx, postCancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer postCancel()
	scannerID, name, pool, err := postClaimWithRetry(
		postCtx, strings.TrimRight(*apiURL, "/"), body,
	)
	if err != nil {
		// Best-effort cleanup so a transient failure doesn't leave
		// behind a stale key the operator has to delete by hand
		// before retrying.
		_ = os.Remove(*keyPath)
		log.Fatalf("claim failed: %v", err)
	}
	if err := agent.WriteScannerID(*idPath, scannerID); err != nil {
		log.Fatalf("persist scanner id: %v", err)
	}
	fmt.Fprintf(os.Stderr, "Claimed as scanner %s (name=%s, pool=%s)\n", scannerID, name, pool)
	fmt.Fprintf(os.Stderr, "  key:  %s\n  id:   %s\n", *keyPath, *idPath)

	if *startAfter {
		// Re-exec ourselves as `agent`. Replacing the process keeps
		// signal handling and logs continuous, vs. starting a child.
		execAgent(*apiURL, scannerID, *keyPath)
	}
}

// postClaim does the POST /api/scanners/claim round-trip and returns
// the assigned (scanner_id, name, pool). Errors include the response
// body so the operator sees the api's reason verbatim ("token has
// expired", "token has already been used", …).
func postClaim(ctx context.Context, apiBase string, body map[string]any) (string, string, string, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return "", "", "", err
	}
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost,
		apiBase+"/api/scanners/claim",
		bytes.NewReader(buf),
	)
	if err != nil {
		return "", "", "", err
	}
	req.Header.Set("Content-Type", "application/json")
	cli := &http.Client{Timeout: 30 * time.Second}
	resp, err := cli.Do(req)
	if err != nil {
		return "", "", "", err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode/100 != 2 {
		err := fmt.Errorf("api returned %d: %s", resp.StatusCode, strings.TrimSpace(string(respBody)))
		// 4xx is permanent (bad token, already used, expired) — don't
		// retry. 5xx + connection errors are transient → retry.
		if resp.StatusCode >= 400 && resp.StatusCode < 500 {
			return "", "", "", &permanentError{err: err}
		}
		return "", "", "", err
	}
	var out struct {
		ScannerID string `json:"scanner_id"`
		Name      string `json:"name"`
		Pool      string `json:"pool"`
	}
	if err := json.Unmarshal(respBody, &out); err != nil {
		return "", "", "", fmt.Errorf("decode response: %w (body: %s)", err, string(respBody))
	}
	return out.ScannerID, out.Name, out.Pool, nil
}

// postClaimWithRetry wraps postClaim with the same exponential
// backoff + jitter that postDiscoverWithRetry uses (see discover.go
// for the rationale). 4xx responses bail out immediately —
// retrying a bad/expired/used token won't change the answer.
func postClaimWithRetry(
	ctx context.Context, apiBase string, body map[string]any,
) (string, string, string, error) {
	delay := time.Second
	const maxDelay = 30 * time.Second
	attempt := 0
	for {
		attempt++
		scannerID, name, pool, err := postClaim(ctx, apiBase, body)
		if err == nil {
			if attempt > 1 {
				fmt.Fprintf(os.Stderr,
					"claim: succeeded on attempt %d\n", attempt)
			}
			return scannerID, name, pool, nil
		}
		var perm *permanentError
		if errors.As(err, &perm) {
			return "", "", "", err
		}
		fmt.Fprintf(os.Stderr,
			"claim: attempt %d failed (%v); retrying in %s\n",
			attempt, err, delay)
		select {
		case <-ctx.Done():
			return "", "", "", fmt.Errorf(
				"claim: gave up after %d attempts: %w", attempt, err,
			)
		case <-time.After(jittered(delay)):
		}
		if delay < maxDelay {
			delay *= 2
			if delay > maxDelay {
				delay = maxDelay
			}
		}
	}
}

// execAgent re-execs the current binary as `akashic-scanner agent …`,
// replacing the process so the caller's PID becomes the agent. Used
// by the --start-after flag of both `claim` and `discover`.
func execAgent(apiURL, scannerID, keyPath string) {
	binary, err := os.Executable()
	if err != nil {
		log.Fatalf("locate self: %v", err)
	}
	args := []string{
		binary, "agent",
		"--api=" + apiURL,
		"--scanner-id=" + scannerID,
		"--key=" + keyPath,
	}
	// Inherit env so docker-compose-style configuration still flows in.
	if err := syscall.Exec(binary, args, os.Environ()); err != nil {
		log.Fatalf("exec agent: %v", err)
	}
}
