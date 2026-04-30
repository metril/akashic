package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// NFSConnector wraps LocalConnector for NFS-mounted paths.
type NFSConnector struct {
	local *LocalConnector
}

func NewNFSConnector() *NFSConnector {
	return &NFSConnector{local: NewLocalConnector()}
}

func (c *NFSConnector) Connect(ctx context.Context) error {
	return c.local.Connect(ctx)
}

func (c *NFSConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, _ bool, fn func(*models.EntryRecord) error) error {
	return walker.Walk(root, excludePatterns, computeHash, fn)
}

func (c *NFSConnector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	return c.local.ReadFile(ctx, path)
}

func (c *NFSConnector) Delete(ctx context.Context, path string) error {
	return c.local.Delete(ctx, path)
}

func (c *NFSConnector) Close() error {
	return nil
}

func (c *NFSConnector) Type() string {
	return "nfs"
}
