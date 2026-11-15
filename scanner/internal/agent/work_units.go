package agent

import (
	"context"
	"crypto/ed25519"
	"errors"
	"fmt"
	"net/http"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// Work-unit endpoints (Phase 2 of v0.5.x parallel scanning).
//
// The agent uses these when the leased scan's MaxParallelScanners > 1.
// Legacy path is unaffected.

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
	priv ed25519.PrivateKey, scanID uuid.UUID,
) (*apiclient.WorkUnitOut, error) {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return nil, err
	}
	resp, err := api.LeaseUnitWithResponse(ctx, scanID)
	if err != nil {
		return nil, err
	}
	switch resp.StatusCode() {
	case http.StatusNoContent:
		return nil, errNoWork
	case http.StatusConflict:
		// Two kinds of 409: the scan itself is terminal (detail.reason ==
		// "scan-terminal" — exit the loop) vs. max_parallel_scanners cap
		// reached (units exist, no slot yet — sleep and retry).
		if detailString(resp.JSON409, "reason") == "scan-terminal" {
			return nil, errScanTerminal
		}
		return nil, fmt.Errorf("%w: %s", errLeaseCap, string(resp.Body))
	case http.StatusOK:
		if resp.JSON200 != nil {
			return resp.JSON200, nil
		}
	}
	return nil, httpError("lease unit", resp.StatusCode(), resp.Body)
}

func splitUnits(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID uuid.UUID, parent *uuid.UUID, childPaths []string,
) (*apiclient.SplitResponse, error) {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return nil, err
	}
	resp, err := api.SplitUnitsWithResponse(ctx, scanID, apiclient.SplitRequest{
		ParentUnitId: parent, ChildPaths: &childPaths,
	})
	if err != nil {
		return nil, err
	}
	if resp.StatusCode() != http.StatusOK || resp.JSON200 == nil {
		return nil, httpError("split", resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

func heartbeatUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID uuid.UUID,
) error {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return err
	}
	resp, err := api.HeartbeatUnitWithResponse(ctx, scanID, unitID)
	if err != nil {
		return err
	}
	if resp.StatusCode() != http.StatusOK {
		return httpError("heartbeat unit", resp.StatusCode(), resp.Body)
	}
	return nil
}

func completeUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID uuid.UUID,
) error {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return err
	}
	resp, err := api.CompleteUnitWithResponse(ctx, scanID, unitID)
	if err != nil {
		return err
	}
	if resp.StatusCode() != http.StatusNoContent {
		return httpError("complete unit", resp.StatusCode(), resp.Body)
	}
	return nil
}

// failUnit reports a unit's walk failure. When requeue is true the API
// puts the unit back on the queue for retry (up to a bounded attempt
// count) instead of marking it permanently failed — used for a transient
// SMB stall so a flaky share doesn't drop the subtree from the index.
// v0.34.0.
func failUnit(
	ctx context.Context, httpc *http.Client, cfg Config,
	priv ed25519.PrivateKey, scanID, unitID uuid.UUID, errMsg string, requeue bool,
) error {
	api, err := newAPI(httpc, cfg, priv)
	if err != nil {
		return err
	}
	resp, err := api.FailUnitWithResponse(ctx, scanID, unitID,
		apiclient.CompleteUnitRequest{ErrorMessage: wire.Str(errMsg), Requeue: wire.To(requeue)})
	if err != nil {
		return err
	}
	if resp.StatusCode() != http.StatusNoContent {
		return httpError("fail unit", resp.StatusCode(), resp.Body)
	}
	return nil
}
