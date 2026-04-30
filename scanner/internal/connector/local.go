package connector

import (
	"context"
	"fmt"
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

func (c *LocalConnector) Walk(_ context.Context, root string, excludePatterns []string, computeHash bool, _ bool, fn func(*models.EntryRecord) error) error {
	return walker.Walk(root, excludePatterns, computeHash, fn)
}

func (c *LocalConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	return os.Open(path)
}

// Delete removes a regular file. Refuses directories defensively — the
// duplicates flow never targets a directory, so anything reaching this
// path with a dir is a logic bug somewhere upstream.
func (c *LocalConnector) Delete(_ context.Context, path string) error {
	st, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if st.IsDir() {
		return fmt.Errorf("refusing to delete directory %q", path)
	}
	return os.Remove(path)
}

func (c *LocalConnector) Close() error {
	return nil
}

func (c *LocalConnector) Type() string {
	return "local"
}
