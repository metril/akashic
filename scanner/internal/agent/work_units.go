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
	// v0.34.0 — requeue the unit (status back to pending) for retry
	// instead of failing it permanently. Set on a transient SMB stall.
	Requeue bool `json:"requeue,omitempty"`
}

// errNoWork is returned by leaseUnit when /work/lease replies 204 — no
// unit is available right now. The scan is NOT necessarily over; sibling
// scanners may still be splitting fresh units, so the caller should
// poll, not exit. (v0.34.0 — the loop is now "sticky".)
var errNoWork = errors.New("no work units available")

// errLeaseCap is wrapped into the error leaseUnit returns on a 409
// (max_parallel_scanners reached). A 409 means units DO exist — there's
// just no slot for this scanner yet — so callers that probe for
// enumeration can tell "capped" apart from a transient failure via
// errors.Is and skip re-enumerating.
var errLeaseCap = errors.New("lease cap reached")

// errScanTerminal is returned by leaseUnit when /work/lease replies 409
// with detail.reason == "scan-terminal" — the scan itself has finished.
// The caller MUST exit the unit loop; no further work will ever appear.
var errScanTerminal = errors.New("scan reached a terminal state")

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
		raw, _ := io.ReadAll(resp.Body)
		// Two kinds of 409: the scan itself is terminal (detail.reason ==
		// "scan-terminal" — exit the loop) vs. max_parallel_scanners cap
		// reached (units exist, no slot yet — sleep and retry).
		var body struct {
			Detail struct {
				Reason string `json:"reason"`
			} `json:"detail"`
		}
		if json.Unmarshal(raw, &body) == nil && body.Detail.Reason == "scan-terminal" {
			return nil, errScanTerminal
		}
		return nil, fmt.Errorf("%w: %s", errLeaseCap, string(raw))
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

// failUnit reports a unit's walk failure. When requeue is true the API
// puts the unit back on the queue for retry (up to a bounded attempt
// count) instead of marking it permanently failed — used for a transient
// SMB stall so a flaky share doesn't drop the subtree from the index.
func failUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID, errMsg string, requeue bool,
) error {
	auth, err := authHeader(cfg, priv)
	if err != nil {
		return err
	}
	url := fmt.Sprintf("%s/api/scans/%s/work/%s/fail",
		cfg.APIBase, scanID, unitID)
	resp, err := doJSON(ctx, httpc, "POST", url, auth,
		failReq{ErrorMessage: errMsg, Requeue: requeue})
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
