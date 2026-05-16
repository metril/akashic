package client

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// Gzip compression threshold for batch bodies. Bodies smaller than this
// don't benefit (compression CPU + decode CPU + headers cost more than
// the bandwidth saved). Real-world batches are 50 KB–1 MB+ — well over
// the threshold — but the final batch on a tiny scan can be ~1 KB,
// where compression is pure overhead. 1 KB matches Python's
// http.client default trigger for similar reasons.
const _gzipMinBodyBytes = 1024

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
	// Retry knobs are exposed for tests; production callers use the
	// defaults installed by New.
	maxAttempts int
	backoffBase time.Duration
}

func New(baseURL, apiKey string) *Client {
	// Tune the transport for a long-lived agent that talks to the
	// same api host repeatedly. Default Go transport caps idle conns
	// per host at 2; that throttles a fast scanner sending 1 k-entry
	// batches every few seconds because each batch ends up on a fresh
	// TCP connection (handshake + TLS). 8 is plenty for the lease +
	// heartbeat + ingest mix.
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConnsPerHost = 8
	t.IdleConnTimeout = 90 * time.Second

	return &Client{
		baseURL: baseURL,
		apiKey:  apiKey,
		httpClient: &http.Client{
			Timeout:   30 * time.Second,
			Transport: t,
		},
		maxAttempts: 4,                      // initial + 3 retries
		backoffBase: 200 * time.Millisecond, // 200ms, 400ms, 800ms (+jitter)
	}
}

// SendBatch posts a scan batch to /api/ingest/batch with retry on
// transient failures (network errors and 5xx responses). 4xx responses
// are treated as terminal — they signal a malformed batch or auth
// problem that won't fix itself.
//
// Pre-retry, a single transient API hiccup killed the whole scan after
// the walker had already done all the work. Idempotency: the api's
// ingest path dedups by (source_id, path), so a retried batch is safe
// even if the first attempt actually committed.
func (c *Client) SendBatch(ctx context.Context, batch models.ScanBatch) (*models.BatchResponse, error) {
	body, err := json.Marshal(batch)
	if err != nil {
		return nil, fmt.Errorf("marshal batch: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		resp, err := c.sendOnce(ctx, body)
		if err == nil {
			return resp, nil
		}
		lastErr = err
		// v0.30.1 — a 413 means the batch is too big for some proxy or
		// server in the request path. Retrying it unchanged can't help;
		// split it into halves and send those (recursively). This
		// recovers the scan instead of failing it after the walker has
		// already done all the work.
		if isPayloadTooLarge(err) {
			return c.sendSplit(ctx, batch, err)
		}
		if !isRetryable(err) {
			return nil, err
		}
		// Exponential backoff with ±25% jitter. Sleeps are
		// context-cancellable so a SIGTERM during a backoff doesn't
		// stall the whole agent.
		if attempt+1 < c.maxAttempts {
			d := c.backoffBase << attempt
			jitter := time.Duration(rand.Int63n(int64(d / 4)))
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(d + jitter):
			}
		}
	}
	return nil, fmt.Errorf("send batch: %d attempts failed: %w", c.maxAttempts, lastErr)
}

// sendSplit handles a 413 by halving batch.Entries and sending each
// half through SendBatch — so a half that's still too big splits
// again. The left half carries the source-security metadata; the
// right half carries IsFinal + the inaccessible counts, so the
// is_final batch stays last (the API requires the terminal batch be
// last). The two BatchResponses are merged, and PayloadSplit is set so
// the sender can feed AIMD a shrink signal even though the send
// ultimately succeeded.
//
// Base case: a single entry that still 413s is dropped with a warning
// — one pathological file (megabytes of ACLs/xattrs) must never fail
// an entire scan. A dropped *final* batch still ships an empty
// is_final batch so the scan terminates cleanly rather than hanging
// until the watchdog.
func (c *Client) sendSplit(ctx context.Context, batch models.ScanBatch, cause error) (*models.BatchResponse, error) {
	if len(batch.Entries) <= 1 {
		if len(batch.Entries) == 1 {
			log.Printf("scanner: dropping oversized entry %q — exceeds the ingest body limit even on its own: %v",
				batch.Entries[0].Path, cause)
		}
		if !batch.IsFinal {
			return &models.BatchResponse{PayloadSplit: true}, nil
		}
		// The final batch must still deliver the is_final marker (and
		// the inaccessible counts) or the scan never terminates. An
		// empty final batch is a few hundred bytes — it always fits.
		empty := batch
		empty.Entries = []models.EntryRecord{}
		empty.SourceSecurityMetadata = nil
		resp, err := c.SendBatch(ctx, empty)
		if err != nil {
			return nil, err
		}
		if resp == nil {
			resp = &models.BatchResponse{}
		}
		resp.PayloadSplit = true
		return resp, nil
	}

	mid := len(batch.Entries) / 2
	left := models.ScanBatch{
		SourceID:               batch.SourceID,
		ScanID:                 batch.ScanID,
		Entries:                batch.Entries[:mid],
		IsFinal:                false,
		SourceSecurityMetadata: batch.SourceSecurityMetadata,
	}
	right := models.ScanBatch{
		SourceID:          batch.SourceID,
		ScanID:            batch.ScanID,
		Entries:           batch.Entries[mid:],
		IsFinal:           batch.IsFinal,
		InaccessibleDirs:  batch.InaccessibleDirs,
		InaccessibleFiles: batch.InaccessibleFiles,
	}

	lresp, err := c.SendBatch(ctx, left)
	if err != nil {
		return nil, err
	}
	rresp, err := c.SendBatch(ctx, right)
	if err != nil {
		return nil, err
	}

	merged := &models.BatchResponse{PayloadSplit: true}
	if lresp != nil {
		merged.ExtractCandidates = append(merged.ExtractCandidates, lresp.ExtractCandidates...)
	}
	if rresp != nil {
		merged.ExtractCandidates = append(merged.ExtractCandidates, rresp.ExtractCandidates...)
	}
	return merged, nil
}

// isPayloadTooLarge reports whether err is (or wraps) a 413.
func isPayloadTooLarge(err error) bool {
	var pt payloadTooLargeError
	return errors.As(err, &pt)
}

// sendOnce performs one POST. Returned errors are wrapped with one of
// the retryable* / terminalStatusError types so SendBatch can decide
// whether to back off or give up.
//
// v0.29.2 — gzip-encodes the JSON body when over `_gzipMinBodyBytes`.
// FastAPI/uvicorn decode `Content-Encoding: gzip` transparently. Real
// scan batches compress 80–90% (JSON is mostly repeated keys + paths
// + ASCII strings), so this typically cuts wire size by ~5–10× — a
// big win for remote scanners on slow uplinks.
func (c *Client) sendOnce(ctx context.Context, body []byte) (*models.BatchResponse, error) {
	reqBody := body
	contentEncoding := ""
	if len(body) >= _gzipMinBodyBytes {
		var gzbuf bytes.Buffer
		gz := gzip.NewWriter(&gzbuf)
		if _, werr := gz.Write(body); werr != nil {
			// In-memory gzip.Write can't fail in practice; the only
			// path is a writer-Close error after the buffer grew
			// past max-int — vastly bigger than any batch. Fall
			// through to uncompressed if it ever does happen.
			gz = nil
		} else if cerr := gz.Close(); cerr == nil {
			reqBody = gzbuf.Bytes()
			contentEncoding = "gzip"
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/ingest/batch", bytes.NewReader(reqBody))
	if err != nil {
		return nil, terminalError{fmt.Errorf("create request: %w", err)}
	}
	req.Header.Set("Content-Type", "application/json")
	if contentEncoding != "" {
		req.Header.Set("Content-Encoding", contentEncoding)
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		// Network-level errors are always retryable: TCP RST, DNS
		// blip, TLS handshake timeout, server-half-closed mid-request.
		return nil, retryableError{fmt.Errorf("send batch: %w", err)}
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 500 {
		// Drain a few bytes so the body can be reused and the error
		// surface includes the server's hint when there is one.
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, retryableError{fmt.Errorf("batch rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	if resp.StatusCode == http.StatusRequestEntityTooLarge {
		// v0.29.6 — 413 is a "your batch is too big" signal, which
		// IS a load signal worth feeding to AIMD even though it's
		// 4xx. Wrap with payloadTooLargeError so the sender can
		// surface it to AdaptiveBatcher.Observe while still treating
		// it as terminal for retry purposes.
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, payloadTooLargeError{fmt.Errorf("batch rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return nil, terminalError{fmt.Errorf("batch rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	// v0.30.0 — decode the response body for extract_candidates. A
	// decode failure on a 200 is non-fatal: the batch WAS accepted;
	// the worst case is this batch's new/changed files don't get
	// extracted until the next scan.
	var br models.BatchResponse
	if err := json.NewDecoder(resp.Body).Decode(&br); err != nil {
		return &models.BatchResponse{}, nil
	}
	return &br, nil
}

// retryableError marks an error that SendBatch should back off and
// retry on. Network failures and 5xx responses qualify.
type retryableError struct{ error }

// terminalError marks an error that won't fix itself — bad request,
// auth failure, malformed body, etc. SendBatch returns immediately.
// Per v0.29.6 these are NOT load signals — AIMD should leave the
// batch size alone (a 422 is a code/config bug, not "server is
// overloaded").
type terminalError struct{ error }

// payloadTooLargeError marks a 413 specifically. As of v0.30.1
// SendBatch (and SendContent) handle it by splitting the batch and
// re-sending the halves rather than failing — see sendSplit /
// sendContentSplit and isPayloadTooLarge.
type payloadTooLargeError struct{ error }

func isRetryable(err error) bool {
	switch err.(type) {
	case retryableError:
		return true
	}
	return false
}

// IsLoadSignal reports whether the error indicates that the batch
// triggered a real load condition on the API path (server overloaded,
// timeout, network failure). AdaptiveBatcher's latency-driven
// multiplicative-decrease (Observe) should only run on these — a 4xx
// is misuse, not overload, so halving the batch makes no sense.
//
// A 413 is deliberately NOT a load signal here: as of v0.30.1
// SendBatch recovers from a 413 by splitting the batch itself, and
// the sender drives AIMD's shrink via NotePayloadTooLarge instead
// (keyed off resp.PayloadSplit). A 413 therefore never reaches this
// predicate as an error.
//
// Walks the error chain (errors.As) so it correctly identifies the
// wrapped form SendBatch returns after retry exhaustion ("send
// batch: 4 attempts failed: %w").
//
// v0.29.6; 413 handling revised v0.30.1.
func IsLoadSignal(err error) bool {
	if err == nil {
		return false
	}
	var rt retryableError
	return errors.As(err, &rt)
}
