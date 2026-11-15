// Package client is the scanner's ingest transport: a thin layer over the
// generated apiclient that adds bearer auth, gzip request bodies, retry with
// backoff, and 413 split-and-resend. Wire shapes come from apiclient.
package client

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// Gzip compression threshold for batch bodies. Bodies smaller than this
// don't benefit (compression CPU + decode CPU + headers cost more than
// the bandwidth saved). Real-world batches are 50 KB–1 MB+ — well over
// the threshold — but the final batch on a tiny scan can be ~1 KB,
// where compression is pure overhead. 1 KB matches Python's
// http.client default trigger for similar reasons.
const _gzipMinBodyBytes = 1024

// errBodyLimit caps how much of an error response body is quoted in errors.
const errBodyLimit = 512

// BatchResult is what SendBatch hands back: the API's extract candidates
// plus a scanner-side flag. It is not an API shape.
type BatchResult struct {
	ExtractCandidates []apiclient.ExtractCandidate
	// PayloadSplit is set by the scanner's HTTP client (not the API)
	// when a 413 forced the batch to be split into smaller sub-batches
	// to get past a reverse-proxy body limit. It is a signal for the
	// sender to tell AIMD to shrink. v0.30.1.
	PayloadSplit bool
}

type Client struct {
	api *apiclient.ClientWithResponses
	// Retry knobs are exposed for tests; production callers use the
	// defaults installed by New.
	maxAttempts int
	backoffBase time.Duration
}

// New builds an ingest client authenticating with the scan's ingest token.
// It fails only when baseURL is not a valid URL.
func New(baseURL, apiKey string) (*Client, error) {
	// Tune the transport for a long-lived agent that talks to the
	// same api host repeatedly. Default Go transport caps idle conns
	// per host at 2; that throttles a fast scanner sending 1 k-entry
	// batches every few seconds because each batch ends up on a fresh
	// TCP connection (handshake + TLS). 8 is plenty for the lease +
	// heartbeat + ingest mix.
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConnsPerHost = 8
	t.IdleConnTimeout = 90 * time.Second

	api, err := apiclient.NewClientWithResponses(baseURL,
		apiclient.WithHTTPClient(&http.Client{Timeout: 30 * time.Second, Transport: t}),
		apiclient.WithRequestEditorFn(BearerEditor(apiKey)),
	)
	if err != nil {
		return nil, fmt.Errorf("api client for %q: %w", baseURL, err)
	}
	return &Client{
		api:         api,
		maxAttempts: 4,                      // initial + 3 retries
		backoffBase: 200 * time.Millisecond, // 200ms, 400ms, 800ms (+jitter)
	}, nil
}

// BearerEditor authenticates each request with a fixed bearer token.
func BearerEditor(token string) apiclient.RequestEditorFn {
	return func(_ context.Context, req *http.Request) error {
		req.Header.Set("Authorization", "Bearer "+token)
		return nil
	}
}

// gzipEditor gzip-encodes the JSON body when over `_gzipMinBodyBytes`.
// FastAPI/uvicorn decode `Content-Encoding: gzip` transparently. Real
// scan batches compress 80–90% (JSON is mostly repeated keys + paths
// + ASCII strings), so this typically cuts wire size by ~5–10× — a
// big win for remote scanners on slow uplinks. v0.29.2.
func gzipEditor(_ context.Context, req *http.Request) error {
	if req.Body == nil {
		return nil
	}
	raw, err := io.ReadAll(req.Body)
	_ = req.Body.Close()
	if err != nil {
		return fmt.Errorf("read request body: %w", err)
	}
	if len(raw) >= _gzipMinBodyBytes {
		var buf bytes.Buffer
		gz := gzip.NewWriter(&buf)
		if _, err := gz.Write(raw); err != nil {
			return fmt.Errorf("gzip request body: %w", err)
		}
		if err := gz.Close(); err != nil {
			return fmt.Errorf("gzip request body: %w", err)
		}
		raw = buf.Bytes()
		req.Header.Set("Content-Encoding", "gzip")
	}
	req.Body = io.NopCloser(bytes.NewReader(raw))
	req.GetBody = func() (io.ReadCloser, error) { return io.NopCloser(bytes.NewReader(raw)), nil }
	req.ContentLength = int64(len(raw))
	return nil
}

// retry runs op with exponential backoff (±25% jitter) on retryable errors.
// Sleeps are context-cancellable so a SIGTERM during a backoff doesn't
// stall the whole agent. Non-retryable errors (including 413, which the
// caller handles by splitting) are returned as-is.
func (c *Client) retry(ctx context.Context, what string, op func() error) error {
	var lastErr error
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		err := op()
		if err == nil {
			return nil
		}
		lastErr = err
		if !isRetryable(err) {
			return err
		}
		if attempt+1 < c.maxAttempts {
			d := c.backoffBase << attempt
			jitter := time.Duration(rand.Int63n(int64(d / 4)))
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(d + jitter):
			}
		}
	}
	return fmt.Errorf("%s: %d attempts failed: %w", what, c.maxAttempts, lastErr)
}

// SendBatch posts a scan batch to /api/v1/ingest/batch with retry on
// transient failures (network errors and 5xx responses). 4xx responses
// are treated as terminal — they signal a malformed batch or auth
// problem that won't fix itself.
//
// Pre-retry, a single transient API hiccup killed the whole scan after
// the walker had already done all the work. Idempotency: the api's
// ingest path dedups by (source_id, path), so a retried batch is safe
// even if the first attempt actually committed.
func (c *Client) SendBatch(ctx context.Context, batch apiclient.ScanBatchIn) (*BatchResult, error) {
	var res *BatchResult
	err := c.retry(ctx, "send batch", func() error {
		r, err := c.sendOnce(ctx, batch)
		res = r
		return err
	})
	if err != nil {
		// v0.30.1 — a 413 means the batch is too big for some proxy or
		// server in the request path. Retrying it unchanged can't help;
		// split it into halves and send those (recursively). This
		// recovers the scan instead of failing it after the walker has
		// already done all the work.
		if isPayloadTooLarge(err) {
			return c.sendSplit(ctx, batch, err)
		}
		return nil, err
	}
	return res, nil
}

// sendSplit handles a 413 by halving batch.Entries and sending each
// half through SendBatch — so a half that's still too big splits
// again. The left half carries the source-security metadata; the
// right half carries IsFinal + the inaccessible counts, so the
// is_final batch stays last (the API requires the terminal batch be
// last). The two results are merged, and PayloadSplit is set so
// the sender can feed AIMD a shrink signal even though the send
// ultimately succeeded.
//
// Base case: a single entry that still 413s is dropped with a warning
// — one pathological file (megabytes of ACLs/xattrs) must never fail
// an entire scan. A dropped *final* batch still ships an empty
// is_final batch so the scan terminates cleanly rather than hanging
// until the watchdog.
func (c *Client) sendSplit(ctx context.Context, batch apiclient.ScanBatchIn, cause error) (*BatchResult, error) {
	isFinal := batch.IsFinal != nil && *batch.IsFinal
	if len(batch.Entries) <= 1 {
		if len(batch.Entries) == 1 {
			log.Printf("scanner: dropping oversized entry %q — exceeds the ingest body limit even on its own: %v",
				batch.Entries[0].Path, cause)
		}
		if !isFinal {
			return &BatchResult{PayloadSplit: true}, nil
		}
		// The final batch must still deliver the is_final marker (and
		// the inaccessible counts) or the scan never terminates. An
		// empty final batch is a few hundred bytes — it always fits.
		empty := batch
		empty.Entries = []apiclient.EntryIn{}
		empty.SourceSecurityMetadata = nil
		res, err := c.SendBatch(ctx, empty)
		if err != nil {
			return nil, err
		}
		if res == nil {
			res = &BatchResult{}
		}
		res.PayloadSplit = true
		return res, nil
	}

	mid := len(batch.Entries) / 2
	left := apiclient.ScanBatchIn{
		SourceId:               batch.SourceId,
		ScanId:                 batch.ScanId,
		Entries:                batch.Entries[:mid],
		SourceSecurityMetadata: batch.SourceSecurityMetadata,
	}
	right := apiclient.ScanBatchIn{
		SourceId:          batch.SourceId,
		ScanId:            batch.ScanId,
		Entries:           batch.Entries[mid:],
		IsFinal:           batch.IsFinal,
		InaccessibleDirs:  batch.InaccessibleDirs,
		InaccessibleFiles: batch.InaccessibleFiles,
	}

	lres, err := c.SendBatch(ctx, left)
	if err != nil {
		return nil, err
	}
	rres, err := c.SendBatch(ctx, right)
	if err != nil {
		return nil, err
	}

	merged := &BatchResult{PayloadSplit: true}
	if lres != nil {
		merged.ExtractCandidates = append(merged.ExtractCandidates, lres.ExtractCandidates...)
	}
	if rres != nil {
		merged.ExtractCandidates = append(merged.ExtractCandidates, rres.ExtractCandidates...)
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
func (c *Client) sendOnce(ctx context.Context, batch apiclient.ScanBatchIn) (*BatchResult, error) {
	httpResp, err := c.api.IngestBatch(ctx, batch, gzipEditor)
	if err != nil {
		// Network-level errors are always retryable: TCP RST, DNS
		// blip, TLS handshake timeout, server-half-closed mid-request.
		return nil, retryableError{fmt.Errorf("send batch: %w", err)}
	}
	// bufferBody reads and closes the body but leaves a re-readable copy, so
	// the generated parser below can still decode it; a decode failure
	// leaves `parsed` nil, which only matters on a 200.
	status := httpResp.StatusCode
	body, err := bufferBody(httpResp)
	if err != nil {
		return nil, retryableError{fmt.Errorf("read batch response: %w", err)}
	}
	if serr := statusError("batch", status, body); serr != nil {
		return nil, serr
	}
	parsed, perr := apiclient.ParseIngestBatchReply(httpResp)
	// v0.30.0 — the response body carries extract_candidates. A decode
	// failure on a 200 is non-fatal: the batch WAS accepted; the worst
	// case is this batch's new/changed files don't get extracted until
	// the next scan.
	if perr != nil || parsed == nil || parsed.JSON200 == nil {
		if perr != nil {
			log.Printf("scanner: batch accepted but response body unreadable: %v", perr)
		}
		return &BatchResult{}, nil
	}
	return &BatchResult{ExtractCandidates: parsed.JSON200.ExtractCandidates}, nil
}

// bufferBody reads the response body in full and leaves a re-readable copy
// in place, so the generated parser can still decode it and the raw bytes
// stay available for error messages even when decoding fails.
func bufferBody(resp *http.Response) ([]byte, error) {
	raw, err := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	resp.Body = io.NopCloser(bytes.NewReader(raw))
	return raw, err
}

// statusError classifies a non-200 status: 5xx is retryable, 413 is
// payload-too-large (drives the split), anything else is terminal. A nil
// return means 200. The body only decorates the message; the status alone
// decides.
func statusError(what string, status int, body []byte) error {
	if status == http.StatusOK {
		return nil
	}
	if len(body) > errBodyLimit {
		body = body[:errBodyLimit]
	}
	err := fmt.Errorf("%s rejected: status %d: %s", what, status, string(body))
	switch {
	case status >= 500:
		return retryableError{err}
	case status == http.StatusRequestEntityTooLarge:
		// v0.29.6 — 413 is a "your batch is too big" signal, which
		// IS a load signal worth feeding to AIMD even though it's
		// 4xx. Wrap with payloadTooLargeError so the sender can
		// surface it to AdaptiveBatcher.Observe while still treating
		// it as terminal for retry purposes.
		return payloadTooLargeError{err}
	default:
		return terminalError{err}
	}
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
