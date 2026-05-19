package connector

import (
	"context"
	"io"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type Connector interface {
	Connect(ctx context.Context) error
	// Walk traverses `root` and emits an EntryRecord per file/directory
	// via fn. WalkStats counts entries the walker silently skipped
	// (permission denied, ENOENT mid-scan, metadata read failures) so
	// the api can surface "N inaccessible items skipped" instead of
	// pretending the scan was clean. Connectors that don't use the
	// local walker (s3, smb's own walker) return zero stats.
	Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fullScan bool, fn func(*models.EntryRecord) error) (walker.WalkStats, error)
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

	// StatRoot returns an EntryRecord for the directory at `path` itself
	// — the record the recursive Walk emits post-order for every
	// directory it enters. A budgeted shallow walk (v0.34.0) walks each
	// directory via WalkShallow, which emits only that directory's
	// FILES, so the unit must emit the directory's own record itself.
	// Connectors where a directory has no meaningful record (S3 — a
	// prefix is not an object) return (nil, nil).
	StatRoot(ctx context.Context, path string) (*models.EntryRecord, error)
}

// TransientStaller is implemented by connectors that can report whether
// a walk failure was a transient stall (e.g. an SMB server that stopped
// responding mid-walk and got its connection force-closed by the op
// guard) rather than a permanent error (permission denied, bad path).
// The unit runner requeues a stalled unit for retry instead of failing
// it permanently and abandoning its subtree.
type TransientStaller interface {
	IsStalled() bool
}
