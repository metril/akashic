package agent

import (
	"context"
	"crypto/ed25519"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
)

// Work-unit endpoints (Phase 2 of v0.5.x parallel scanning).
//
// The agent uses these when the leased scan's MaxParallelScanners > 1.
// Legacy path is unaffected — these wire types stay unused for
// max_parallel_scanners == 1.

type workUnit struct {
	ID              string `json:"id"`
	ScanID          string `json:"scan_id"`
	Path            string `json:"path"`
	Status          string `json:"status"`
	LeaseExpiresAt  string `json:"lease_expires_at"`
}

type splitReq struct {
	ParentUnitID *string  `json:"parent_unit_id,omitempty"`
	ChildPaths   []string `json:"child_paths"`
}

type splitResp struct {
	Created int `json:"created"`
	Skipped int `json:"skipped"`
}

type failReq struct {
	ErrorMessage string `json:"error_message,omitempty"`
}

// errNoWork is returned by leaseUnit when /work/lease replies 204.
// Caller treats it as "this scan is drained for me, exit the loop".
var errNoWork = errors.New("no work units available")

func leaseUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID string,
) (*workUnit, error) {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return nil, err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/lease", cfg.APIBase, scanID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, struct{}{})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNoContent {
		return nil, errNoWork
	}
	if resp.StatusCode == http.StatusConflict {
		// max_parallel_scanners cap reached. Caller should sleep and
		// retry — eventually a holder finishes a unit and frees a slot.
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("lease cap: %s", string(raw))
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("lease unit HTTP %d: %s", resp.StatusCode, string(raw))
	}
	var u workUnit
	if err := json.NewDecoder(resp.Body).Decode(&u); err != nil {
		return nil, fmt.Errorf("decode unit: %w", err)
	}
	return &u, nil
}

func splitUnits(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID string, parent *string, childPaths []string,
) (*splitResp, error) {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return nil, err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/split", cfg.APIBase, scanID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, splitReq{
		ParentUnitID: parent, ChildPaths: childPaths,
	})
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("split HTTP %d: %s", resp.StatusCode, string(raw))
	}
	var sr splitResp
	if err := json.NewDecoder(resp.Body).Decode(&sr); err != nil {
		return nil, fmt.Errorf("decode split: %w", err)
	}
	return &sr, nil
}

func heartbeatUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID string,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/%s/heartbeat",
		cfg.APIBase, scanID, unitID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, struct{}{})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("heartbeat unit HTTP %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}

func completeUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID string,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/%s/complete",
		cfg.APIBase, scanID, unitID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, struct{}{})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("complete unit HTTP %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}

func failUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID, errMsg string,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/%s/fail",
		cfg.APIBase, scanID, unitID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth, failReq{ErrorMessage: errMsg})
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		raw, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("fail unit HTTP %d: %s", resp.StatusCode, string(raw))
	}
	return nil
}
