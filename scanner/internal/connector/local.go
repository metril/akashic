package connector

import (
	"context"
	"io"
	"os"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type LocalConnector struct{}

func NewLocalConnector() *LocalConnector {
	return &LocalConnector{}
}

func (c *LocalConnector) Connect(_ context.Context) error {
	return nil
}

func (c *LocalConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	return walker.Walk(root, excludePatterns, computeHash, fn)
}

func (c *LocalConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return os.Open(path)
}

func (c *LocalConnector) Close() error {
	return nil
}

func (c *LocalConnector) Type() string {
	return "local"
}
