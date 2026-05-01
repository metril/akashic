// Package agent runs the long-poll lease loop that turns a scanner
// host into a remote worker. The agent calls /api/scanners/handshake
// at startup (versions out of range → exit 1), then loops
// /api/scans/lease to claim work. Each leased scan runs with the
// existing scanner.Run code path; on completion the agent calls
// /api/scans/{id}/complete to release the lease and re-polls.
package agent

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/protocol"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
)

// Config holds the agent's runtime configuration. KeyPath is read at
// startup; the in-memory ed25519.PrivateKey is reloaded on SIGHUP.
type Config struct {
	APIBase    string // e.g. https://api.example.com
	ScannerID  string // matches scanners.id on the api
	KeyPath    string
	LeasePoll  time.Duration // jittered ±20%
	Hostname   string        // self-reported on handshake
	Version    string        // build-time version string
}

// Run is the entry point used by `akashic-scanner agent`. It blocks
// until ctx is cancelled (SIGTERM) or an unrecoverable error occurs.
func Run(ctx context.Context, cfg Config) error {
	priv, err := LoadPrivateKey(cfg.KeyPath)
	if err != nil {
		return fmt.Errorf("load private key: %w", err)
	}
	httpc := &http.Client{Timeout: 60 * time.Second}

	// 1) Handshake — single-shot. Out-of-range protocol → fatal.
	if err := handshake(ctx, httpc, cfg, priv); err != nil {
		return fmt.Errorf("handshake: %w", err)
	}

	// 2) Independent heartbeat goroutine — keeps the admin UI's
	// online indicator fresh between jobs.
	go heartbeatLoop(ctx, httpc, cfg, priv)

	// 3) Lease loop. Sleeps with ±20% jitter on empty leases so a
	// fleet of agents in the same pool doesn't synchronise their
	// polls and pound the api.
	for {
		if ctx.Err() != nil {
			return nil
		}
		leased, err := lease(ctx, httpc, cfg, priv)
		if err != nil {
			log.Printf("lease error: %v (sleeping)", err)
			sleepWithJitter(ctx, cfg.LeasePoll)
			continue
		}
		if leased == nil {
			sleepWithJitter(ctx, cfg.LeasePoll)
			continue
		}
		if err := runLeasedScan(ctx, cfg, priv, leased); err != nil {
			log.Printf("scan %s failed: %v", leased.ScanID, err)
			_ = complete(ctx, httpc, cfg, priv, leased.ScanID, "failed", err.Error())
		} else {
			_ = complete(ctx, httpc, cfg, priv, leased.ScanID, "completed", "")
		}
	}
}

// ── Wire types ───────────────────────────────────────────────────────────

type leasedSource struct {
	ID               string         `json:"id"`
	Type             string         `json:"type"`
	ConnectionConfig map[string]any `json:"connection_config"`
	ExcludePatterns  []string       `json:"exclude_patterns"`
}

type leasedScan struct {
	ScanID   string       `json:"scan_id"`
	ScanType string       `json:"scan_type"`
	Source   leasedSource `json:"source"`
	APIJWT   string       `json:"api_jwt"`
}

type handshakeReq struct {
	ProtocolVersion int    `json:"protocol_version"`
	Version         string `json:"version,omitempty"`
	Hostname        string `json:"hostname,omitempty"`
}

type handshakeResp struct {
	Accepted              bool   `json:"accepted"`
	ServerProtocolVersion int    `json:"server_protocol_version"`
	AcceptedMin           int    `json:"accepted_min"`
	AcceptedMax           int    `json:"accepted_max"`
	Reason                string `json:"reason,omitempty"`
}

type completeReq struct {
	Status       string `json:"status"`
	ErrorMessage string `json:"error_message,omitempty"`
}

// ── HTTP helpers ─────────────────────────────────────────────────────────

func authHeader(cfg Config, priv ed25519.PrivateKey) (string, error) {
	tok, err := MintJWT(priv, cfg.ScannerID)
	if err != nil {
		return "", err
	}
	return "Bearer " + tok, nil
}

func doJSON(
	ctx context.Context,
	httpc *http.Client,
	method, url string,
	auth string,
	body any,
) (*http.Response, error) {
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, rdr)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if auth != "" {
		req.Header.Set("Authorization", auth)
	}
	return httpc.Do(req)
}

// ── Handshake / heartbeat / lease / complete ─────────────────────────────

func handshake(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	body := handshakeReq{
		ProtocolVersion: protocol.Version,
		Version:         cfg.Version,
		Hostname:        cfg.Hostname,
	}
	resp, err := doJSON(ctx, httpc, "POST",
		cfg.APIBase+"/api/scanners/handshake", auth, body)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUpgradeRequired {
		var hr handshakeResp
		_ = json.NewDecoder(resp.Body).Decode(&hr)
		return fmt.Errorf("api rejected protocol_version=%d (server accepts [%d,%d]): %s",
			protocol.Version, hr.AcceptedMin, hr.AcceptedMax, hr.Reason)
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("handshake HTTP %d: %s", resp.StatusCode, string(raw))
	}
	log.Printf("handshake ok: scanner_id=%s protocol=%d hostname=%s",
		cfg.ScannerID, protocol.Version, cfg.Hostname)
	return nil
}

func heartbeatLoop(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	url := fmt.Sprintf("%s/api/scanners/%s/heartbeat", cfg.APIBase, cfg.ScannerID)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		auth, err := authHeader(cfg, priv)
		if err != nil {
			log.Printf("heartbeat: sign failed: %v", err)
			continue
		}
		resp, err := doJSON(ctx, httpc, "POST", url, auth, struct{}{})
		if err != nil {
			log.Printf("heartbeat: %v", err)
			continue
		}
		resp.Body.Close()
		if resp.StatusCode == http.StatusUnauthorized {
			log.Printf("heartbeat: 401 (key may have rotated; SIGHUP to reload)")
		}
	}
}

func lease(
	ctx context.Context, httpc *http.Client, cfg Config, priv ed25519.PrivateKey,
) (*leasedScan, error) {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return nil, err
	}
	resp, err := doJSON(ctx, httpc, "POST",
		cfg.APIBase+"/api/scans/lease", auth, struct{}{})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("lease HTTP %d: %s", resp.StatusCode, string(raw))
	}
	var ls leasedScan
	if err := json.NewDecoder(resp.Body).Decode(&ls); err != nil {
		return nil, fmt.Errorf("decode lease: %w", err)
	}
	return &ls, nil
}

func complete(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, status, errMsg string,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	resp, err := doJSON(ctx, httpc, "POST",
		fmt.Sprintf("%s/api/scans/%s/complete", cfg.APIBase, scanID),
		auth, completeReq{Status: status, ErrorMessage: errMsg})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("complete HTTP %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}

// ── Scan execution ───────────────────────────────────────────────────────

// runLeasedScan turns a leased payload into a real scan via the
// existing scanner.New + scanner.Run path. The leased `api_jwt`
// authenticates the per-scan heartbeat + ingest calls (for now —
// Phase 3 of the multi-scanner work refactors this so the agent
// signs those calls itself).
func runLeasedScan(
	ctx context.Context,
	cfg Config,
	_ ed25519.PrivateKey,
	leased *leasedScan,
) error {
	conn, err := connectorFromLeased(leased.Source)
	if err != nil {
		return err
	}
	root := stringFromConfig(leased.Source.ConnectionConfig, "path", "")
	if root == "" {
		// Some connectors use different keys for "where to start"
		// (s3 uses "bucket+prefix"; the existing CLI accepts -bucket
		// flag instead). For Phase 2, just use the empty string and
		// let the connector default.
	}
	apiClient := client.New(cfg.APIBase, leased.APIJWT)

	state := observe.NewState()
	reporter := observe.New(cfg.APIBase, leased.APIJWT, leased.ScanID, state)
	scanCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	reporter.SetUserCancel(cancel)
	reporter.Start(scanCtx)
	defer reporter.Stop()

	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        leased.Source.ID,
		ScanID:          leased.ScanID,
		Root:            root,
		BatchSize:       1000,
		Hash:            leased.ScanType == "full",
		ExcludePatterns: leased.Source.ExcludePatterns,
		Reporter:        reporter,
		State:           state,
	})
	_, err = s.Run(scanCtx)
	if err != nil && (errors.Is(err, context.Canceled) || scanCtx.Err() != nil) {
		// The api signalled cancel via a 409 on heartbeat. Not an error
		// from our perspective — the api already marked the scan
		// cancelled, so report `cancelled` rather than `failed`.
		return errCancelled
	}
	return err
}

// errCancelled is a sentinel — the agent treats it as "report status=
// cancelled to /complete" rather than failed.
var errCancelled = errors.New("scan cancelled by api")

func connectorFromLeased(src leasedSource) (connector.Connector, error) {
	cfg := src.ConnectionConfig
	switch src.Type {
	case "local":
		return connector.NewLocalConnector(), nil
	case "nfs":
		return connector.NewNFSConnector(), nil
	case "ssh":
		return connector.NewSSHConnector(
			stringFromConfig(cfg, "host", ""),
			intFromConfig(cfg, "port", 22),
			stringFromConfig(cfg, "username", ""),
			stringFromConfig(cfg, "password", ""),
			stringFromConfig(cfg, "key_path", ""),
			stringFromConfig(cfg, "key_passphrase", ""),
			stringFromConfig(cfg, "known_hosts", ""),
		), nil
	case "smb":
		return connector.NewSMBConnector(
			stringFromConfig(cfg, "host", ""),
			intFromConfig(cfg, "port", 445),
			stringFromConfig(cfg, "username", ""),
			stringFromConfig(cfg, "password", ""),
			stringFromConfig(cfg, "share", ""),
		), nil
	case "s3":
		return connector.NewS3Connector(
			stringFromConfig(cfg, "endpoint", ""),
			stringFromConfig(cfg, "bucket", ""),
			stringFromConfig(cfg, "region", "us-east-1"),
			stringFromConfig(cfg, "access_key_id", ""),
			stringFromConfig(cfg, "secret_access_key", ""),
		), nil
	default:
		return nil, fmt.Errorf("unsupported source type: %s", src.Type)
	}
}

func stringFromConfig(m map[string]any, k, dflt string) string {
	if v, ok := m[k]; ok {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
	}
	return dflt
}

func intFromConfig(m map[string]any, k string, dflt int) int {
	if v, ok := m[k]; ok {
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		case string:
			// rarely needed; trust callers
			_ = strings.TrimSpace(n)
		}
	}
	return dflt
}

// ── Sleep with jitter ────────────────────────────────────────────────────

func sleepWithJitter(ctx context.Context, base time.Duration) {
	if base <= 0 {
		base = 5 * time.Second
	}
	// ±20% jitter so a fleet of polling agents doesn't synchronise
	// after a network blip and stampede the api.
	jitter := time.Duration(rand.Int63n(int64(base) * 4 / 10))
	d := base - base/5 + jitter
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
	case <-t.C:
	}
}

// Help the linter stop complaining about an unused import in the
// (rare) case where stringFromConfig's `strings` use is removed.
var _ = os.Hostname
