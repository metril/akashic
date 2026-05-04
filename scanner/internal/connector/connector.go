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

// ShallowWalker is implemented by connectors that can list immediate
// children of a directory without recursing into subdirectories. The
// unit-coordinated agent uses this for top-level enumeration so
// sibling scanners can fan out across subtrees.
//
// fn is called for each FILE found at root level (subdirectories are
// returned via the SubdirNames slice instead, so the caller can split
// them off as work units rather than walking them inline).
//
// Connectors that don't implement this interface fall back to the
// legacy single-walker path even when max_parallel_scanners > 1.
type ShallowWalker interface {
	WalkShallow(
		ctx context.Context,
		root string,
		excludePatterns []string,
		computeHash bool,
		fn func(*models.EntryRecord) error,
	) (subdirs []string, err error)
}
