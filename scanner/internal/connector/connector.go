package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Connector interface {
	Connect(ctx context.Context) error
	Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.EntryRecord) error) error
	ReadFile(ctx context.Context, path string) (io.ReadCloser, error)
	Close() error
	Type() string
}
