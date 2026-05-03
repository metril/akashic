package walker

import (
	"context"
	"os"
	"path/filepath"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type WalkFunc func(entry *models.EntryRecord) error

// Walk traverses `root` and emits EntryRecord values for every file AND
// directory it visits (the root itself is skipped).
//
// Phase B — switched from filepath.WalkDir (pre-order) to a manual
// recursive DFS (post-order on directories) so each directory record
// can be emitted with its own per-subtree totals already populated.
// Files emit immediately; the parent directory record emits after all
// of its children have been walked, with SubtreeSizeBytes /
// SubtreeFileCount / SubtreeDirCount filled in. This lets the API
// skip the post-scan rollup CTE for any directory the connector
// already aggregated.
//
// Errors from individual entries are swallowed (matches the previous
// behaviour) so a single permission-denied subdirectory doesn't kill
// the whole scan. ctx cancellation, however, is honored — the walk
// returns ctx.Err() at the next directory boundary so a SIGTERM /
// scan-cancel actually stops in-flight 10M+ scans.
func Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn WalkFunc) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	owners := metadata.NewOwnerResolver()

	// We don't emit the root itself — `walkDir` returns its totals to
	// nowhere. Real code only cares about descendants.
	_, err := walkDir(ctx, root, root, excludeSet, computeHash, owners, fn)
	return err
}

// subtreeTotals captures what a recursive call returns to its parent so
// the parent can fold child contributions into its own SubtreeSize* fields.
type subtreeTotals struct {
	bytes     int64
	fileCount int64
	dirCount  int64
}

// walkDir is the recursive worker. For directories it descends, then
// emits the directory record post-order with subtree fields populated.
// For files (callers don't actually pass files here — dispatch happens
// inside) the file record is emitted in-line during the parent's child
// iteration.
//
// Returns the totals for THIS subtree so the caller can sum them.
func walkDir(
	ctx context.Context,
	path string,
	root string,
	excludeSet map[string]bool,
	computeHash bool,
	owners *metadata.OwnerResolver,
	fn WalkFunc,
) (subtreeTotals, error) {
	var totals subtreeTotals

	if err := ctx.Err(); err != nil {
		return totals, err
	}

	entries, err := os.ReadDir(path)
	if err != nil {
		// Permission denied / race with deletion — same swallow
		// behaviour as the old WalkDir. Subtree totals stay at zero,
		// directory still gets emitted by our caller with what it knows.
		return totals, nil
	}

	for _, d := range entries {
		name := d.Name()
		if excludeSet[strings.ToLower(name)] {
			continue
		}
		childPath := filepath.Join(path, name)
		info, err := d.Info()
		if err != nil {
			continue
		}

		if d.IsDir() {
			// Recurse first, then emit the child directory's record
			// with its accumulated totals.
			childTotals, cerr := walkDir(ctx, childPath, root, excludeSet, computeHash, owners, fn)
			if cerr != nil {
				// Propagate cancellation up; permission errors are
				// already swallowed at the recursion site.
				return totals, cerr
			}
			totals.bytes += childTotals.bytes
			totals.fileCount += childTotals.fileCount
			totals.dirCount += childTotals.dirCount + 1 // +1 for the child dir itself

			entry, err := metadata.CollectFromInfo(childPath, info, computeHash, owners)
			if err != nil {
				continue
			}
			// Stamp the child directory's subtree fields and emit it.
			b, f, dn := childTotals.bytes, childTotals.fileCount, childTotals.dirCount
			entry.SubtreeSizeBytes = &b
			entry.SubtreeFileCount = &f
			entry.SubtreeDirCount = &dn
			if err := fn(entry); err != nil {
				return totals, err
			}
		} else {
			// Files emit pre-order (no children to wait for) and
			// contribute their size to this directory's totals.
			entry, err := metadata.CollectFromInfo(childPath, info, computeHash, owners)
			if err != nil {
				continue
			}
			if entry.SizeBytes != nil {
				totals.bytes += *entry.SizeBytes
			}
			totals.fileCount++
			if err := fn(entry); err != nil {
				return totals, err
			}
		}
	}

	return totals, nil
}
