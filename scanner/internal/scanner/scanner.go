package scanner

import (
	"context"
	"fmt"
	"log"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
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
	if err := s.connector.Connect(ctx); err != nil {
		return nil, fmt.Errorf("connect: %w", err)
	}
	defer s.connector.Close()

	var bucketSecurity *models.SourceSecurityMetadata
	if s3c, ok := s.connector.(*connector.S3Connector); ok {
		if s.opts.CaptureObjectACLs {
			s3c.SetCaptureObjectACLs(true)
		}
		if sec, err := s3c.CollectBucketSecurity(ctx); err == nil {
			bucketSecurity = sec
		} else {
			log.Printf("warning: bucket security capture failed: %v", err)
		}
	}

	result := &Result{}
	var batch []models.EntryRecord
	firstBatch := true

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

	err := s.connector.Walk(ctx, s.opts.Root, s.opts.ExcludePatterns, walkHash, func(entry *models.EntryRecord) error {
		if entry.IsDir() {
			result.DirsFound++
		} else {
			result.FilesFound++

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

		if len(batch) >= s.opts.BatchSize {
			return flush(false)
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk: %w", err)
	}

	if err := flush(true); err != nil {
		return nil, err
	}

	log.Printf("scan complete: %d files, %d dirs, %d batches", result.FilesFound, result.DirsFound, result.BatchesSent)
	return result, nil
}
