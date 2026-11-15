package client

import (
	"context"
	"fmt"
	"log"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// SendContent posts a batch of extracted-text records to
// /api/v1/ingest/content with the same retry / gzip / auth behaviour as
// SendBatch. v0.30.0.
//
// A content-send failure is non-fatal to a scan: the metadata is
// already ingested; the worst case is some files' text isn't
// searchable until the next scan re-extracts them. Callers log the
// error and continue.
func (c *Client) SendContent(ctx context.Context, batch apiclient.ContentBatchIn) error {
	err := c.retry(ctx, "send content", func() error { return c.sendContentOnce(ctx, batch) })
	// v0.30.1 — a 413 means the content batch is too big for a
	// proxy in the path. Split it and send the halves rather than
	// giving up on the whole batch.
	if err != nil && isPayloadTooLarge(err) {
		return c.sendContentSplit(ctx, batch, err)
	}
	return err
}

// sendContentSplit handles a 413 on a content batch by halving its
// items and sending each half through SendContent (recursively, so a
// half that's still too big splits again). A single item that still
// 413s is dropped with a warning — content extraction is best-effort,
// so one un-shippable file's text is an acceptable loss, never a scan
// failure. v0.30.1.
func (c *Client) sendContentSplit(ctx context.Context, batch apiclient.ContentBatchIn, cause error) error {
	if len(batch.Items) <= 1 {
		if len(batch.Items) == 1 {
			log.Printf("scanner: dropping content for %q — extracted text exceeds the ingest body limit even on its own: %v",
				batch.Items[0].Path, cause)
		}
		return nil
	}
	mid := len(batch.Items) / 2
	left := apiclient.ContentBatchIn{SourceId: batch.SourceId, ScanId: batch.ScanId, Items: batch.Items[:mid]}
	right := apiclient.ContentBatchIn{SourceId: batch.SourceId, ScanId: batch.ScanId, Items: batch.Items[mid:]}
	if err := c.SendContent(ctx, left); err != nil {
		return err
	}
	return c.SendContent(ctx, right)
}

func (c *Client) sendContentOnce(ctx context.Context, batch apiclient.ContentBatchIn) error {
	resp, err := c.api.IngestContent(ctx, batch, gzipEditor)
	if err != nil {
		return retryableError{fmt.Errorf("send content: %w", err)}
	}
	status := resp.StatusCode
	body, err := bufferBody(resp)
	if err != nil {
		return retryableError{fmt.Errorf("read content response: %w", err)}
	}
	return statusError("content", status, body)
}
