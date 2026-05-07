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
	"sync"
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
	// Reuse one HTTP client with keepalive across handshake / heartbeat /
	// lease / complete. Default `http.Client{}` would close the
	// connection between every request — wasteful at heartbeat cadence.
	httpc := &http.Client{
		Timeout:   60 * time.Second,
		Transport: newKeepaliveTransport(),
	}

	// 1) Handshake — single-shot. Out-of-range protocol → fatal.
	if err := handshake(ctx, httpc, cfg, priv); err != nil {
		return fmt.Errorf("handshake: %w", err)
	}

	// 2) Independent heartbeat goroutine — keeps the admin UI's
	// online indicator fresh between jobs.
	go heartbeatLoop(ctx, httpc, cfg, priv)

	// 2a) Independent reachability poll goroutine. Claims and runs
	// probes from /api/scanners/{id}/reachability/poll. Decoupled
	// from the scan lease cadence so a long scan doesn't starve the
	// reachability data path. v0.5.7.
	go reachabilityLoop(ctx, httpc, cfg, priv)

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
		if shouldUseUnits(leased) {
			// Unit-coordinated path: this scan is opted in for parallel
			// scanning. The work-units API drives terminal status; we
			// don't call /api/scans/{id}/complete ourselves (that would
			// overwrite the auto-finalization).
			if err := runUnitCoordinated(ctx, httpc, cfg, priv, leased); err != nil {
				log.Printf("unit-scan %s failed: %v", leased.ScanID, err)
			}
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

// shouldUseUnits decides whether this leased scan goes down the
// work-unit-coordinated path. Connector type isn't checked here —
// the runner type-asserts ShallowWalker on the built connector and
// falls back to legacy if a future connector hasn't implemented it.
// All shipped connectors (local, nfs, ssh, smb, s3) implement it.
func shouldUseUnits(leased *leasedScan) bool {
	return leased.Source.MaxParallelScanners > 1
}

// newKeepaliveTransport returns an *http.Transport tuned for a long-
// lived agent calling the same api host repeatedly. The Go default
// transport caps idle conns per host at 2; we raise it because lease
// + heartbeat + (occasionally) complete can race.
func newKeepaliveTransport() *http.Transport {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConnsPerHost = 8
	t.IdleConnTimeout = 90 * time.Second
	return t
}

// ── Wire types ───────────────────────────────────────────────────────────

type leasedSource struct {
	ID                   string         `json:"id"`
	Type                 string         `json:"type"`
	ConnectionConfig     map[string]any `json:"connection_config"`
	ExcludePatterns      []string       `json:"exclude_patterns"`
	MaxParallelScanners  int            `json:"max_parallel_scanners"`
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

// jwtCache caches the most recently minted bearer header. The api
// accepts JWTs for 5 minutes (see MintJWT); we re-mint when the
// remaining lifetime drops below jwtRefreshAt — early enough that an
// in-flight request never carries a token that expires mid-flight.
//
// Pre-cache, every authHeader call (heartbeat every 30 s, plus lease /
// complete) re-signed the token even though Ed25519 signatures are
// cheap. Cleaner contract for downstream code, and saves a few
// microseconds per call too.
type jwtCache struct {
	mu        sync.Mutex
	header    string
	expiresAt time.Time
}

const (
	jwtTTL       = 5 * time.Minute // matches MintJWT's exp claim
	jwtRefreshAt = 1 * time.Minute // remint when ≤1 minute remains
)

// agentTokenCache is process-global for an agent — there is exactly
// one identity (cfg.ScannerID + priv) per agent process, so a single
// cache is sufficient. A fleshier design would key by (scannerID,
// pubkey-fingerprint), but that's overkill until SIGHUP'd key
// rotation actually swaps the in-memory key.
var agentTokenCache jwtCache

func authHeader(cfg Config, priv ed25519.PrivateKey) (string, error) {
	agentTokenCache.mu.Lock()
	defer agentTokenCache.mu.Unlock()
	if agentTokenCache.header != "" && time.Until(agentTokenCache.expiresAt) > jwtRefreshAt {
		return agentTokenCache.header, nil
	}
	tok, err := MintJWT(priv, cfg.ScannerID)
	if err != nil {
		return "", err
	}
	agentTokenCache.header = "Bearer " + tok
	agentTokenCache.expiresAt = time.Now().Add(jwtTTL)
	return agentTokenCache.header, nil
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
	case "smb":
		return connector.NewSMBConnector(
			stringFromConfig(cfg, "host", ""),
			intFromConfig(cfg, "port", 445),
			stringFromConfig(cfg, "username", ""),
			stringFromConfig(cfg, "password", ""),
			stringFromConfig(cfg, "share", ""),
		), nil
	case "s3":
		s3conn := connector.NewS3Connector(
			stringFromConfig(cfg, "endpoint", ""),
			stringFromConfig(cfg, "bucket", ""),
			stringFromConfig(cfg, "region", "us-east-1"),
			stringFromConfig(cfg, "access_key_id", ""),
			stringFromConfig(cfg, "secret_access_key", ""),
		)
		// v0.8.1 — explicit path_style override (tri-state). The web
		// preset dropdown writes path_style=false for Wasabi/B2 (which
		// expect virtual-hosted-style with their endpoint set) and
		// path_style=true for MinIO. Absent key falls through to the
		// connector's auto-derive (path-style when endpoint != "").
		if v, ok := cfg["path_style"]; ok {
			if b, ok := v.(bool); ok {
				s3conn.SetPathStyle(&b)
			}
		}
		return s3conn, nil
	case "paperless":
		// v0.7.0 — Tier 3 self-hosted libraries. Hostless: url +
		// api_token live on the source's connection_config.
		// tag_filter is comma-separated; tls_verify defaults to true
		// (api/router scrubs the value to bool).
		return connector.NewPaperlessConnector(
			stringFromConfig(cfg, "url", ""),
			stringFromConfig(cfg, "api_token", ""),
			splitCommaList(stringFromConfig(cfg, "tag_filter", "")),
			boolFromConfig(cfg, "tls_verify", true),
		), nil
	case "immich":
		// v0.8.0 — Tier 3 self-hosted libraries. Hostless. URL +
		// api_key live on the source. include_archived defaults
		// false to mirror the Immich UI's archive-hides-from-grid
		// behaviour. album_filter is a comma-separated whitelist
		// of album NAMES (case-insensitive).
		return connector.NewImmichConnector(
			stringFromConfig(cfg, "url", ""),
			stringFromConfig(cfg, "api_key", ""),
			splitCommaList(stringFromConfig(cfg, "album_filter", "")),
			boolFromConfig(cfg, "include_archived", false),
			boolFromConfig(cfg, "tls_verify", true),
		), nil
	case "webdav":
		// v0.11.0 — Tier 4 PR 1. Hostless. URL + basic auth creds
		// on the source. Covers Nextcloud, ownCloud, Synology File
		// Station, generic Apache mod_dav, sabredav.
		return connector.NewWebDAVConnector(
			stringFromConfig(cfg, "url", ""),
			stringFromConfig(cfg, "username", ""),
			stringFromConfig(cfg, "password", ""),
			boolFromConfig(cfg, "tls_verify", true),
		), nil
	case "gdrive":
		// v0.14.0 — Tier 1 PR-C. OAuth-shaped: access_token is minted
		// at lease time by the API from the source's connected
		// SourceOAuthCredential row. FolderID is optional; empty ==
		// walk My Drive root.
		return connector.NewGDriveConnector(&connector.GDriveConfig{
			AccessToken: stringFromConfig(cfg, "access_token", ""),
			FolderID:    stringFromConfig(cfg, "folder_id", ""),
		}), nil
	case "onedrive":
		// v0.15.0 — Tier 1 PR-C part 2. OAuth-shaped via Microsoft
		// Graph. Same access-token mechanism as gdrive. ItemID is
		// optional; empty == walk OneDrive root.
		return connector.NewOneDriveConnector(&connector.OneDriveConfig{
			AccessToken: stringFromConfig(cfg, "access_token", ""),
			ItemID:      stringFromConfig(cfg, "item_id", ""),
		}), nil
	case "dropbox":
		// v0.17.0 — Tier 4 PR 2. OAuth via the Dropbox provider in
		// the OAuth registry. Path-based addressing (no native_id
		// juggling). Empty path == scan from the root.
		return connector.NewDropboxConnector(&connector.DropboxConfig{
			AccessToken: stringFromConfig(cfg, "access_token", ""),
			Path:        stringFromConfig(cfg, "path", ""),
		}), nil
	default:
		return nil, fmt.Errorf("unsupported source type: %s", src.Type)
	}
}

// splitCommaList parses a "a, b , c" string into ["a","b","c"], skipping
// empties. Used by hostless connectors that take their filter list as a
// single connection_config string.
func splitCommaList(s string) []string {
	if s == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			out = append(out, p)
		}
	}
	return out
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

func boolFromConfig(m map[string]any, k string, dflt bool) bool {
	if v, ok := m[k]; ok {
		switch b := v.(type) {
		case bool:
			return b
		case string:
			s := strings.ToLower(strings.TrimSpace(b))
			if s == "false" || s == "0" || s == "no" {
				return false
			}
			if s == "true" || s == "1" || s == "yes" {
				return true
			}
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
