package scanner

import (
	"context"
	"sync"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/extract"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// Content-batch flush thresholds — a SendContent fires when either is
// crossed, amortizing HTTP round-trips while keeping each body small.
const (
	contentBatchMaxItems = 50
	contentBatchMaxBytes = 1 << 20 // 1 MiB of extracted text
)

// contentSink batches extract.ContentRecords from the extraction pool
// and POSTs them to /api/ingest/content. Safe for concurrent Add from
// multiple pool workers.
type contentSink struct {
	mu       sync.Mutex
	ctx      context.Context
	client   *client.Client
	sourceID string
	scanID   string
	items    []models.ContentItem
	nbytes   int
	logf     func(string, ...any)
}

func newContentSink(
	ctx context.Context, c *client.Client, sourceID, scanID string,
	logf func(string, ...any),
) *contentSink {
	return &contentSink{ctx: ctx, client: c, sourceID: sourceID, scanID: scanID, logf: logf}
}

func (s *contentSink) add(rec extract.ContentRecord) {
	s.mu.Lock()
	s.items = append(s.items, models.ContentItem{
		Path: rec.Path, ContentText: rec.ContentText,
	})
	s.nbytes += len(rec.ContentText)
	if len(s.items) >= contentBatchMaxItems || s.nbytes >= contentBatchMaxBytes {
		batch := s.items
		s.items = nil
		s.nbytes = 0
		s.mu.Unlock()
		s.send(batch)
		return
	}
	s.mu.Unlock()
}

func (s *contentSink) flush() {
	s.mu.Lock()
	batch := s.items
	s.items = nil
	s.nbytes = 0
	s.mu.Unlock()
	if len(batch) > 0 {
		s.send(batch)
	}
}

func (s *contentSink) send(items []models.ContentItem) {
	err := s.client.SendContent(s.ctx, models.ContentBatch{
		SourceID: s.sourceID, ScanID: s.scanID, Items: items,
	})
	if err != nil {
		s.logf("content send failed (%d items): %v", len(items), err)
	}
}

// DriveExtraction runs text extraction for a fixed set of candidate
// files and ships the results to /api/ingest/content. It is a
// best-effort post-pass: a connect/extract/send failure is logged,
// never fatal. `conn` must NOT be pre-connected — DriveExtraction
// owns its lifecycle (a dedicated connector instance, separate from
// the walk's, so SMB sessions don't contend).
//
// Returns (filesExtracted, failures).
func DriveExtraction(
	ctx context.Context,
	apiClient *client.Client,
	ex *extract.Extractor,
	conn connector.Connector,
	workers int,
	sourceID, scanID string,
	candidates []models.ExtractCandidate,
	logf func(string, ...any),
) (extracted int64, failures int64) {
	if ex == nil || conn == nil || len(candidates) == 0 {
		return 0, 0
	}
	if err := conn.Connect(ctx); err != nil {
		logf("extraction skipped: connect failed: %v", err)
		return 0, 0
	}
	defer conn.Close()

	sink := newContentSink(ctx, apiClient, sourceID, scanID, logf)
	pool := extract.NewPool(conn, ex, workers, sink.add, logf)
	for _, c := range candidates {
		if ctx.Err() != nil {
			break
		}
		if extract.IsEligible(c.MimeType, c.SizeBytes) {
			pool.Submit(extract.Job{Path: c.Path, MimeType: c.MimeType, Size: c.SizeBytes})
		}
	}
	pool.Close()
	sink.flush()
	return pool.Stats()
}
