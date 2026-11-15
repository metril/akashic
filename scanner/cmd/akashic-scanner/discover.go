package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/scanner/internal/agent"
	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// runDiscover implements `akashic-scanner discover` — the
// scanner-initiated discovery flow used when no join token is
// available. The scanner generates a keypair, posts its public key +
// hostname to /api/v1/scanners/discover, prints the pairing code on
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

	// Retry the initial POST on transient failures (api still
	// starting up, network blip mid-restart, etc.). The user's
	// --timeout bounds the total wait. 4xx responses are NOT
	// retried — they're our problem (bad input) or a hard "no"
	// from the server (discovery disabled), not transients.
	postCtx, postCancel := context.WithTimeout(context.Background(), *timeout)
	defer postCancel()
	discoveryID, pairing, expiresAt, err := postDiscoverWithRetry(
		postCtx, apiBase, apiclient.DiscoverRequest{
			PublicKeyPem:  pub,
			Hostname:      wire.Str(host),
			AgentVersion:  wire.To(Version),
			RequestedPool: wire.Str(*pool),
		},
	)
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

func postDiscover(ctx context.Context, apiBase string, body apiclient.DiscoverRequest) (uuid.UUID, string, time.Time, error) {
	api, err := apiclient.NewClientWithResponses(apiBase, apiclient.WithHTTPClient(&http.Client{Timeout: 30 * time.Second}))
	if err != nil {
		return uuid.Nil, "", time.Time{}, &permanentError{err: err}
	}
	resp, err := api.DiscoverScannerWithResponse(ctx, body)
	if err != nil {
		return uuid.Nil, "", time.Time{}, err
	}
	if resp.StatusCode()/100 != 2 {
		// 404 = discovery disabled on the server; phrase the error so
		// the operator can find the toggle.
		if resp.StatusCode() == 404 {
			return uuid.Nil, "", time.Time{}, fmt.Errorf(
				"discovery endpoint returned 404 — is `discovery_enabled` turned on in the api's Settings → Scanners pane?",
			)
		}
		err := fmt.Errorf("api returned %d: %s", resp.StatusCode(), strings.TrimSpace(string(resp.Body)))
		// 4xx is "the api heard us and said no" — bad input, discovery
		// disabled, etc. Don't retry; the operator has to fix something.
		// 5xx + connection errors are transient → retry.
		if resp.StatusCode() >= 400 && resp.StatusCode() < 500 {
			return uuid.Nil, "", time.Time{}, &permanentError{err: err}
		}
		return uuid.Nil, "", time.Time{}, err
	}
	if resp.JSON201 == nil {
		return uuid.Nil, "", time.Time{}, fmt.Errorf("discover: unexpected %d response (body: %s)", resp.StatusCode(), string(resp.Body))
	}
	return resp.JSON201.DiscoveryId, resp.JSON201.PairingCode, resp.JSON201.ExpiresAt, nil
}

// permanentError marks a postDiscover failure as not-retryable
// (4xx response — the api has decided "no", retrying won't change
// the answer).
type permanentError struct{ err error }

func (e *permanentError) Error() string { return e.err.Error() }
func (e *permanentError) Unwrap() error { return e.err }

// postDiscoverWithRetry wraps postDiscover with exponential backoff +
// jitter so a scanner that came up before the api is accepting
// connections (cold start, mid-restart, …) eventually succeeds
// instead of crash-looping. Bounded by ctx (the user's --timeout).
//
// Backoff: 1s → 2s → 4s → 8s → 16s → 30s (capped). Jitter ±20%
// so a fleet of scanners coming up together doesn't stampede.
func postDiscoverWithRetry(
	ctx context.Context, apiBase string, body apiclient.DiscoverRequest,
) (uuid.UUID, string, time.Time, error) {
	delay := time.Second
	const maxDelay = 30 * time.Second
	attempt := 0
	for {
		attempt++
		discoveryID, pairing, expiresAt, err := postDiscover(ctx, apiBase, body)
		if err == nil {
			if attempt > 1 {
				fmt.Fprintf(os.Stderr,
					"discover: succeeded on attempt %d\n", attempt)
			}
			return discoveryID, pairing, expiresAt, nil
		}
		// Don't retry permanent errors — printing the same "discovery
		// disabled" message every 2s is just noise.
		var perm *permanentError
		if errors.As(err, &perm) {
			return uuid.Nil, "", time.Time{}, err
		}
		fmt.Fprintf(os.Stderr,
			"discover: attempt %d failed (%v); retrying in %s\n",
			attempt, err, delay)
		select {
		case <-ctx.Done():
			return uuid.Nil, "", time.Time{}, fmt.Errorf(
				"discover: gave up after %d attempts: %w", attempt, err,
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

// jittered adds ±20% noise to `d` so a fleet of scanners restarting
// together doesn't synchronise their retries into a thundering herd.
// math/rand (not crypto/rand) is fine — this is timing jitter, not
// security material.
func jittered(d time.Duration) time.Duration {
	noise := time.Duration(float64(d) * 0.2 * (2*rand.Float64() - 1)) //nolint:gosec
	return d + noise
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
func isTransientPollStatus(code int) bool {
	return code == 502 || code == 503 || code == 504
}

// pollBackoff: 1s, 2s, 4s … capped at 15s.
func pollBackoff(attempt int) time.Duration {
	if attempt > 4 {
		attempt = 4
	}
	d := time.Second << attempt
	if d > 15*time.Second {
		d = 15 * time.Second
	}
	return d
}

// sleepCtx sleeps d; false if ctx ended first.
func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func pollDiscover(ctx context.Context, apiBase string, discoveryID uuid.UUID) (string, string, string) {
	api, err := apiclient.NewClientWithResponses(apiBase,
		apiclient.WithHTTPClient(&http.Client{Timeout: 35 * time.Second})) // > server long-poll window
	if err != nil {
		log.Fatalf("build poll client: %v", err)
	}
	transient := 0
	for {
		select {
		case <-ctx.Done():
			fmt.Fprintln(os.Stderr, "discovery timed out before an admin decided")
			os.Exit(66)
		default:
		}
		resp, err := api.DiscoverStatusWithResponse(ctx, discoveryID)
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
		if isTransientPollStatus(resp.StatusCode()) {
			fmt.Fprintf(os.Stderr, "poll returned %d (will retry)\n", resp.StatusCode())
			if !sleepCtx(ctx, pollBackoff(transient)) {
				os.Exit(66)
			}
			transient++
			continue
		}
		transient = 0
		if resp.StatusCode()/100 != 2 || resp.JSON200 == nil {
			fmt.Fprintf(os.Stderr, "poll returned %d: %s\n", resp.StatusCode(), strings.TrimSpace(string(resp.Body)))
			os.Exit(1)
		}
		st := resp.JSON200
		switch st.Status {
		case "approved":
			if st.ScannerId == nil {
				log.Fatalf("discovery approved without a scanner_id")
			}
			return st.ScannerId.String(), wire.Deref(st.Name), wire.Deref(st.Pool)
		case "denied":
			fmt.Fprintf(os.Stderr, "admin denied discovery")
			if reason := wire.Deref(st.DenyReason); reason != "" {
				fmt.Fprintf(os.Stderr, ": %s", reason)
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
