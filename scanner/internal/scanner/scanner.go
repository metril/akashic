package scanner

import (
	"context"
	"fmt"
	"log"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Options struct {
	SourceID        string
	ScanID          string
	Root            string
	BatchSize       int
	Hash            bool
	ExcludePatterns []string
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

	result := &Result{}
	var batch []models.FileEntry

	flush := func(final bool) error {
		if len(batch) == 0 && !final {
			return nil
		}
		scanBatch := models.ScanBatch{
			SourceID: s.opts.SourceID,
			ScanID:   s.opts.ScanID,
			Files:    batch,
			IsFinal:  final,
		}
		if err := s.client.SendBatch(ctx, scanBatch); err != nil {
			return fmt.Errorf("send batch: %w", err)
		}
		result.BatchesSent++
		batch = nil
		return nil
	}

	err := s.connector.Walk(ctx, s.opts.Root, s.opts.ExcludePatterns, s.opts.Hash, func(entry *models.FileEntry) error {
		if entry.IsDir {
			result.DirsFound++
		} else {
			result.FilesFound++
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
