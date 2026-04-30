package scanner

import (
	"context"
	"fmt"
	"log"
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
}

type Result struct {
	FilesFound  int
	DirsFound   int
	BatchesSent int
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
	// Connect() blocks for several seconds (SMB share auth, NFS mount,
	// SSH handshake). The previous flow was silent until the walk
	// actually began, which on a slow share looked indistinguishable
	// from "the scanner isn't running at all".
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

	flush := func(final bool) error {
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
		if err := s.client.SendBatch(ctx, scanBatch); err != nil {
			// Same reasoning as the Connect path above: emit through
			// the LogSink before returning so the user sees WHY the
			// scan died, not just that it did. The api side typically
			// returns a structured error (HTTP status + body); SendBatch
			// folds those into the err string, so logging %v is enough.
			s.warn("send batch failed: %v", err)
			return fmt.Errorf("send batch: %w", err)
		}
		result.BatchesSent++
		batch = nil
		return nil
	}

	// Incremental scans walk without hashing and selectively re-hash files
	// modified after LastScanTime.
	incremental := s.opts.Hash && s.opts.LastScanTime != nil
	walkHash := s.opts.Hash && !incremental
	fullScan := !incremental

	err := s.connector.Walk(ctx, s.opts.Root, s.opts.ExcludePatterns, walkHash, fullScan, func(entry *models.EntryRecord) error {
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
				r, err := s.connector.ReadFile(ctx, entry.Path)
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

		if len(batch) >= s.opts.BatchSize {
			return flush(false)
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk: %w", err)
	}

	s.setPhase("finalize")
	if err := flush(true); err != nil {
		return nil, err
	}

	s.info("scan complete: %d files, %d dirs, %d batches",
		result.FilesFound, result.DirsFound, result.BatchesSent)
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
