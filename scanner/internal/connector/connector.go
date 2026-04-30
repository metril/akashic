package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Connector interface {
	Connect(ctx context.Context) error
	Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fullScan bool, fn func(*models.EntryRecord) error) error
	ReadFile(ctx context.Context, path string) (io.ReadCloser, error)
	// Delete removes a single regular file at `path`. Implementations
	// must NOT recurse into directories — bulk-delete is for the Duplicates
	// flow which only ever deletes files. The error string returned should
	// be human-readable (callers surface it in the api response).
	Delete(ctx context.Context, path string) error
	Close() error
	Type() string
}
