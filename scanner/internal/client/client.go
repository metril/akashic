package client

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
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
func (c *Client) SendBatch(ctx context.Context, batch models.ScanBatch) error {
	body, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("marshal batch: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		err := c.sendOnce(ctx, body)
		if err == nil {
			return nil
		}
		lastErr = err
		if !isRetryable(err) {
			return err
		}
		// Exponential backoff with ±25% jitter. Sleeps are
		// context-cancellable so a SIGTERM during a backoff doesn't
		// stall the whole agent.
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
	return fmt.Errorf("send batch: %d attempts failed: %w", c.maxAttempts, lastErr)
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
func (c *Client) sendOnce(ctx context.Context, body []byte) error {
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
		return terminalError{fmt.Errorf("create request: %w", err)}
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
		return retryableError{fmt.Errorf("send batch: %w", err)}
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 500 {
		// Drain a few bytes so the body can be reused and the error
		// surface includes the server's hint when there is one.
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return retryableError{fmt.Errorf("batch rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return terminalError{fmt.Errorf("batch rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	return nil
}

// retryableError marks an error that SendBatch should back off and
// retry on. Network failures and 5xx responses qualify.
type retryableError struct{ error }

// terminalError marks an error that won't fix itself — bad request,
// auth failure, malformed body, etc. SendBatch returns immediately.
type terminalError struct{ error }

func isRetryable(err error) bool {
	_, ok := err.(retryableError)
	return ok
}
