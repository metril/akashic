package extract

import (
	"context"
	"io"
	"sync"
	"sync/atomic"

	"github.com/akashic-project/akashic/scanner/internal/connector"
)

// Job is one file queued for content extraction.
type Job struct {
	Path     string
	MimeType string
	Size     int64
}

// ContentRecord is the extracted text for one file, emitted to the
// pool's sink. ContentText may be "" (file extracted to nothing) —
// the sink still ships it so the API can clear stale content.
type ContentRecord struct {
	Path        string
	ContentText string
}

// DefaultWorkers is the extraction pool size when not overridden.
const DefaultWorkers = 4

// Pool is a bounded set of worker goroutines that read file content
// via a connector, extract text, and emit ContentRecords to a sink.
// Extraction is best-effort: every per-file failure (unreadable file,
// Tika error, oversize) is logged and swallowed — a scan never fails
// because extraction failed.
type Pool struct {
	conn  connector.Connector
	ex    *Extractor
	jobs  chan Job
	sink  func(ContentRecord)
	logf  func(string, ...any)
	wg    sync.WaitGroup
	extracted atomic.Int64
	failures  atomic.Int64
}

// clampWorkers bounds the worker count by connector type. SMB shares a
// single go-smb2 session; high read fan-out on one session is
// counterproductive, so SMB is clamped low.
func clampWorkers(connType string, requested int) int {
	if requested <= 0 {
		requested = DefaultWorkers
	}
	if connType == "smb" && requested > 2 {
		return 2
	}
	return requested
}

// NewPool starts an extraction pool. conn must already be Connect()ed
// by the caller (the caller also owns Close()ing it). The pool runs
// until Close() is called.
func NewPool(
	conn connector.Connector,
	ex *Extractor,
	workers int,
	sink func(ContentRecord),
	logf func(string, ...any),
) *Pool {
	n := clampWorkers(conn.Type(), workers)
	p := &Pool{
		conn: conn,
		ex:   ex,
		// Bounded buffer: a saturated pool applies brief backpressure
		// to the submitter rather than unbounded memory growth.
		jobs: make(chan Job, n*4),
		sink: sink,
		logf: logf,
	}
	p.wg.Add(n)
	for i := 0; i < n; i++ {
		go p.worker()
	}
	return p
}

func (p *Pool) worker() {
	defer p.wg.Done()
	for job := range p.jobs {
		p.process(job)
	}
}

func (p *Pool) process(job Job) {
	ctx := context.Background()
	rc, err := p.conn.ReadFile(ctx, job.Path)
	if err != nil {
		p.failures.Add(1)
		p.logf("extract: read %s failed: %v", job.Path, err)
		return
	}
	// Cap the read at MaxExtractionSize+1 — defense in depth against a
	// stale connector-reported size; IsEligible already gated on Size.
	content, err := io.ReadAll(io.LimitReader(rc, MaxExtractionSize+1))
	rc.Close()
	if err != nil {
		p.failures.Add(1)
		p.logf("extract: read %s failed: %v", job.Path, err)
		return
	}
	if int64(len(content)) > MaxExtractionSize {
		p.logf("extract: skipping %s — exceeds %d bytes", job.Path, MaxExtractionSize)
		return
	}
	text, err := p.ex.Extract(ctx, content, job.MimeType)
	if err != nil {
		p.failures.Add(1)
		p.logf("extract: %s (%s): %v", job.Path, job.MimeType, err)
		return
	}
	p.extracted.Add(1)
	if text != "" {
		p.sink(ContentRecord{Path: job.Path, ContentText: text})
	}
}

// Submit queues a file for extraction. Blocks briefly if the pool's
// buffer is full (intentional backpressure).
func (p *Pool) Submit(job Job) {
	p.jobs <- job
}

// Close stops accepting jobs and waits for all in-flight extraction
// to drain.
func (p *Pool) Close() {
	close(p.jobs)
	p.wg.Wait()
}

// Stats returns cumulative (filesExtracted, failures) since the pool
// started. Safe to call after Close().
func (p *Pool) Stats() (extracted int64, failures int64) {
	return p.extracted.Load(), p.failures.Load()
}
