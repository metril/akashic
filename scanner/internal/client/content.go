package client

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// SendContent posts a batch of extracted-text records to
// /api/ingest/content with the same retry / gzip / auth behaviour as
// SendBatch. v0.30.0.
//
// A content-send failure is non-fatal to a scan: the metadata is
// already ingested; the worst case is some files' text isn't
// searchable until the next scan re-extracts them. Callers log the
// error and continue.
func (c *Client) SendContent(ctx context.Context, batch models.ContentBatch) error {
	body, err := json.Marshal(batch)
	if err != nil {
		return fmt.Errorf("marshal content batch: %w", err)
	}
	var lastErr error
	for attempt := 0; attempt < c.maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return err
		}
		err := c.sendContentOnce(ctx, body)
		if err == nil {
			return nil
		}
		lastErr = err
		// v0.30.1 — a 413 means the content batch is too big for a
		// proxy in the path. Split it and send the halves rather than
		// giving up on the whole batch.
		if isPayloadTooLarge(err) {
			return c.sendContentSplit(ctx, batch, err)
		}
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
	return fmt.Errorf("send content: %d attempts failed: %w", c.maxAttempts, lastErr)
}

// sendContentSplit handles a 413 on a content batch by halving its
// items and sending each half through SendContent (recursively, so a
// half that's still too big splits again). A single item that still
// 413s is dropped with a warning — content extraction is best-effort,
// so one un-shippable file's text is an acceptable loss, never a scan
// failure. v0.30.1.
func (c *Client) sendContentSplit(ctx context.Context, batch models.ContentBatch, cause error) error {
	if len(batch.Items) <= 1 {
		if len(batch.Items) == 1 {
			log.Printf("scanner: dropping content for %q — extracted text exceeds the ingest body limit even on its own: %v",
				batch.Items[0].Path, cause)
		}
		return nil
	}
	mid := len(batch.Items) / 2
	left := models.ContentBatch{
		SourceID: batch.SourceID, ScanID: batch.ScanID, Items: batch.Items[:mid],
	}
	right := models.ContentBatch{
		SourceID: batch.SourceID, ScanID: batch.ScanID, Items: batch.Items[mid:],
	}
	if err := c.SendContent(ctx, left); err != nil {
		return err
	}
	return c.SendContent(ctx, right)
}

func (c *Client) sendContentOnce(ctx context.Context, body []byte) error {
	reqBody := body
	contentEncoding := ""
	if len(body) >= _gzipMinBodyBytes {
		var gzbuf bytes.Buffer
		gz := gzip.NewWriter(&gzbuf)
		if _, werr := gz.Write(body); werr == nil {
			if cerr := gz.Close(); cerr == nil {
				reqBody = gzbuf.Bytes()
				contentEncoding = "gzip"
			}
		}
	}
	req, err := http.NewRequestWithContext(
		ctx, http.MethodPost, c.baseURL+"/api/ingest/content", bytes.NewReader(reqBody),
	)
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
		return retryableError{fmt.Errorf("send content: %w", err)}
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 500 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return retryableError{fmt.Errorf("content rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	if resp.StatusCode == http.StatusRequestEntityTooLarge {
		// v0.30.1 — 413 drives SendContent's split-and-retry.
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return payloadTooLargeError{fmt.Errorf("content rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return terminalError{fmt.Errorf("content rejected: status %d: %s", resp.StatusCode, string(raw))}
	}
	return nil
}
