package scanner

import (
	"context"
	"fmt"
	"log"
	"sync/atomic"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Options struct {
	SourceID          string
	ScanID            string
	Root              string
	BatchSize         int
	Hash              bool
	ExcludePatterns   []string
	LastScanTime      *time.Time // nil = full scan, non-nil = incremental
	CaptureObjectACLs bool       // S3 only: call GetObjectAcl per file (opt-in)
	// Phase 1 — pre-walk count pass to set total_estimated for ETA. Only
	// useful for first scans; subsequent scans use previous_scan_files.
	Prewalk bool
	// Phase 1 — observability hooks. nil disables live progress reporting
	// (useful for tests / standalone manual runs).
	Reporter *observe.Reporter
	State    *observe.State
	// v0.29.2 — when non-nil, the walker reads the current batch size
	// from AdaptiveBatcher.Current() on every flush check, and the
	// sender goroutine calls AdaptiveBatcher.Observe() after each
	// SendBatch. The static BatchSize is ignored when this is set.
	AdaptiveBatcher *AdaptiveBatchSize
}

type Result struct {
	FilesFound  int
	DirsFound   int
	BatchesSent int
	// v0.5.11 — entries the connector silently skipped during walk.
	// Either a directory we couldn't enter (permission denied, ENOENT
	// mid-scan) or a file we couldn't stat. The api persists these on
	// the Scan row so SourceDetail can surface "N inaccessible items
	// skipped" instead of pretending the scan was clean.
	InaccessibleDirs  int
	InaccessibleFiles int
}

type Scanner struct {
	client    *client.Client
	connector connector.Connector
	opts      Options
}

func New(apiClient *client.Client, conn connector.Connector, opts Options) *Scanner {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 1000
	}
	return &Scanner{
		client:    apiClient,
		connector: conn,
		opts:      opts,
	}
}

func (s *Scanner) Run(ctx context.Context) (*Result, error) {
	// Emit BEFORE Connect so the user sees life immediately, even when
	// Connect() blocks for several seconds (SMB share auth, NFS mount).
	// The previous flow was silent until the walk actually began, which
	// on a slow share looked indistinguishable from "the scanner isn't
	// running at all".
	s.info("connecting to source at %s", s.opts.Root)
	if err := s.connector.Connect(ctx); err != nil {
		// Surface the failure through the structured log sink BEFORE
		// returning. Without this, a connection error reaches
		// main.go's log.Fatalf and the user sees the panel go silent
		// for 60 s before the watchdog fires "scan failed" with a
		// generic timeout message — the actual cause stays hidden in
		// the api container log.
		s.warn("connect failed: %v", err)
		return nil, fmt.Errorf("connect: %w", err)
	}
	defer s.connector.Close()
	s.info("connected; preparing to walk")

	var bucketSecurity *models.SourceSecurityMetadata
	if s3c, ok := s.connector.(*connector.S3Connector); ok {
		if s.opts.CaptureObjectACLs {
			s3c.SetCaptureObjectACLs(true)
		}
		if sec, err := s3c.CollectBucketSecurity(ctx); err == nil {
			bucketSecurity = sec
		} else {
			s.warn("bucket security capture failed: %v", err)
		}
	}

	// Phase 1: prewalk pass for ETA. Only runs on local-style filesystems
	// (where the walker can actually count cheaply); skip for non-local
	// connectors where every entry is a network round-trip.
	if s.opts.Prewalk && s.opts.Root != "" {
		s.setPhase("prewalk")
		s.info("prewalk starting: %s", s.opts.Root)
		pres, err := walker.Prewalk(s.opts.Root, s.opts.ExcludePatterns,
			func(files, _, _ int64, currentPath string) {
				if s.opts.State != nil {
					s.opts.State.SetTotalEstimated(files)
					if currentPath != "" {
						s.opts.State.SetCurrent(currentPath, "prewalk")
					}
				}
			}, 500)
		if err != nil {
			s.warn("prewalk failed (continuing without estimate): %v", err)
		} else {
			if s.opts.State != nil {
				s.opts.State.SetTotalEstimated(pres.Files)
			}
			s.info("prewalk complete: %d files, %d dirs, %d bytes",
				pres.Files, pres.Dirs, pres.Bytes)
		}
	}

	s.setPhase("walk")
	s.info("walk starting: %s", s.opts.Root)

	result := &Result{}
	var batch []models.EntryRecord
	firstBatch := true

	// Progress-log throttle: emit a "scanned N files (current path)" line
	// no more than once per progressLogInterval. Without this, long
	// scans go silent for minutes between the "walk starting" and "scan
	// complete" messages, and the user reasonably wonders if the
	// scanner is doing anything. Threshold is per-message-type, not a
	// per-event count, so a fast NVMe and a slow SMB share both produce
	// readable cadence.
	const progressLogInterval = 3 * time.Second
	var lastProgressLog time.Time

	// v0.29.2 — pipeline the walker and sender. Pre-fix the walker
	// synchronously blocked on SendBatch's HTTP round trip (typically
	// 200–600 ms per batch on a real network), which left the walker
	// idle 12–50% of the time on fast storage. Now the walker pushes
	// completed batches onto a bounded channel and continues; a single
	// sender goroutine drains the channel and POSTs.
	//
	// Buffer size 3: enough to absorb one slow batch without backing
	// up the walker, small enough that memory under worst-case batch
	// size (5000 entries × ~5 KB/entry on SMB-with-ACLs ≈ 25 MB) stays
	// bounded — three buffered batches ≈ 75 MB worst case.
	//
	// Final-batch invariant: flushFinal closes batchCh and waits for
	// the sender to drain; we then return the sender's first error
	// (if any). The InaccessibleDirs/Files counts that v0.5.11 only
	// attached to the final batch still ride on it — the walker
	// stamps them on the final batch before the close.
	batchCh := make(chan models.ScanBatch, 3)
	senderDone := make(chan struct{})
	scanCtx, cancelScan := context.WithCancel(ctx)
	defer cancelScan()
	var firstSendErr atomic.Value // error

	// v0.29.2 — seed the State with the initial batch size so the
	// very first heartbeat carries it (without this, the Live Log
	// row tooltip would say "—" until the first batch lands).
	if s.opts.AdaptiveBatcher != nil && s.opts.State != nil {
		s.opts.State.SetCurrentBatchSize(s.opts.AdaptiveBatcher.Current())
	}

	go func() {
		defer close(senderDone)
		for scanBatch := range batchCh {
			start := time.Now()
			err := s.client.SendBatch(scanCtx, scanBatch)
			elapsed := time.Since(start)
			if s.opts.AdaptiveBatcher != nil {
				s.opts.AdaptiveBatcher.Observe(elapsed, err)
				if s.opts.State != nil {
					s.opts.State.SetCurrentBatchSize(s.opts.AdaptiveBatcher.Current())
				}
			}
			if err != nil {
				if firstSendErr.Load() == nil {
					firstSendErr.Store(err)
				}
				// Cancel the walker — no point producing more batches
				// when the sender is failing.
				cancelScan()
				s.warn("send batch failed: %v", err)
				continue
			}
			result.BatchesSent++
			log.Printf("scan %s: batch %d sent (%d entries, final=%v) in %s",
				s.opts.ScanID, result.BatchesSent, len(scanBatch.Entries),
				scanBatch.IsFinal, elapsed.Round(time.Millisecond))
		}
	}()

	// enqueue moves the in-progress batch onto batchCh, returns
	// without blocking on the network. Resets batch to a fresh slice
	// (don't reuse — the sender goroutine now owns the old backing
	// array). When final=true, attaches the inaccessible counts.
	enqueue := func(final bool) error {
		if len(batch) == 0 && !final {
			return nil
		}
		scanBatch := models.ScanBatch{
			SourceID: s.opts.SourceID,
			ScanID:   s.opts.ScanID,
			Entries:  batch,
			IsFinal:  final,
		}
		if firstBatch {
			scanBatch.SourceSecurityMetadata = bucketSecurity
			firstBatch = false
		}
		if final {
			scanBatch.InaccessibleDirs = result.InaccessibleDirs
			scanBatch.InaccessibleFiles = result.InaccessibleFiles
		}
		select {
		case batchCh <- scanBatch:
		case <-scanCtx.Done():
			return scanCtx.Err()
		}
		batch = nil
		return nil
	}

	// Incremental scans walk without hashing and selectively re-hash files
	// modified after LastScanTime.
	incremental := s.opts.Hash && s.opts.LastScanTime != nil
	walkHash := s.opts.Hash && !incremental
	fullScan := !incremental

	walkStats, err := s.connector.Walk(scanCtx, s.opts.Root, s.opts.ExcludePatterns, walkHash, fullScan, func(entry *models.EntryRecord) error {
		if entry.IsDir() {
			result.DirsFound++
			if s.opts.State != nil {
				s.opts.State.IncDirWalked()
				s.opts.State.SetCurrent(entry.Path, "")
			}
		} else {
			result.FilesFound++
			if s.opts.State != nil {
				s.opts.State.IncFile()
				if entry.SizeBytes != nil {
					s.opts.State.AddBytes(*entry.SizeBytes)
				}
				s.opts.State.SetCurrent(entry.Path, "")
			}

			if incremental && entry.ModifiedAt != nil && !entry.ModifiedAt.Before(*s.opts.LastScanTime) {
				r, err := s.connector.ReadFile(scanCtx, entry.Path)
				if err == nil {
					hash, herr := metadata.HashReader(r)
					r.Close()
					if herr == nil {
						entry.ContentHash = hash
					}
				}
			}
		}

		batch = append(batch, *entry)

		if now := time.Now(); now.Sub(lastProgressLog) >= progressLogInterval {
			s.info("scanned %d files, %d dirs · current: %s",
				result.FilesFound, result.DirsFound, entry.Path)
			lastProgressLog = now
		}

		// v0.29.2 — adaptive threshold. When AdaptiveBatcher is set,
		// the walker reads Current() on every check so a mid-scan
		// adjustment takes effect on the very next batch boundary.
		threshold := s.opts.BatchSize
		if s.opts.AdaptiveBatcher != nil {
			threshold = s.opts.AdaptiveBatcher.Current()
		}
		if len(batch) >= threshold {
			return enqueue(false)
		}
		return nil
	})
	if err != nil {
		// Cancel and close the channel so the sender's range loop
		// exits, then wait for drain — without close(batchCh) the
		// sender keeps blocking on the channel forever and the wait
		// below deadlocks.
		cancelScan()
		close(batchCh)
		<-senderDone
		// Prefer the walker's error if there is one; otherwise the
		// sender's first error explains the cancel.
		if sendErr, _ := firstSendErr.Load().(error); sendErr != nil && err == scanCtx.Err() {
			return nil, fmt.Errorf("send batch: %w", sendErr)
		}
		return nil, fmt.Errorf("walk: %w", err)
	}

	result.InaccessibleDirs = walkStats.InaccessibleDirs
	result.InaccessibleFiles = walkStats.InaccessibleFiles

	s.setPhase("finalize")
	if err := enqueue(true); err != nil {
		cancelScan()
		close(batchCh)
		<-senderDone
		return nil, err
	}
	close(batchCh)
	<-senderDone
	if sendErr, _ := firstSendErr.Load().(error); sendErr != nil {
		return nil, fmt.Errorf("send batch: %w", sendErr)
	}

	s.info("scan complete: %d files, %d dirs, %d batches, %d inaccessible dirs, %d inaccessible files",
		result.FilesFound, result.DirsFound, result.BatchesSent,
		result.InaccessibleDirs, result.InaccessibleFiles)
	return result, nil
}

// info / warn / error route through the structured log sink when one is
// available so the UI sees the lines, falling back to stdlib `log` when
// the scanner is run standalone (no Reporter configured).
func (s *Scanner) info(format string, args ...any) {
	if s.opts.Reporter != nil {
		s.opts.Reporter.LogSink().Info(format, args...)
		return
	}
	log.Printf(format, args...)
}

func (s *Scanner) warn(format string, args ...any) {
	if s.opts.Reporter != nil {
		s.opts.Reporter.LogSink().Warn(format, args...)
		return
	}
	log.Printf("warn: "+format, args...)
}

func (s *Scanner) setPhase(phase string) {
	if s.opts.State != nil {
		s.opts.State.SetCurrent("", phase)
	}
}
