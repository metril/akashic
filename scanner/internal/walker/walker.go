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

// WalkStats tracks how many directory and file reads were silently
// skipped during a walk. v0.5.11 — before this, ENOENT / permission
// errors during walking were swallowed with no record. The scanner
// now ships these counts up to the api in the IsFinal batch envelope
// so SourceDetail can surface "N inaccessible items skipped" instead
// of pretending the scan was clean.
type WalkStats struct {
	// Directory reads that failed (os.ReadDir returned an error or
	// d.Info() on a directory entry failed). Each represents a subtree
	// the scanner couldn't enter.
	InaccessibleDirs int
	// File entries that we couldn't read metadata for (d.Info() or
	// CollectFromInfo failed on a non-directory entry).
	InaccessibleFiles int
}

// ShallowResult is what WalkShallow returns: file/empty-dir entries
// emitted in-line via fn, plus the relative names of subdirectories
// the caller should split off as separate work units instead of
// recursing into.
type ShallowResult struct {
	// Names of immediate subdirectories under root (basename only,
	// no leading "/"). The caller turns these into work-unit paths.
	SubdirNames []string
	// Stats accumulated during the shallow walk (current dir only —
	// subdirs are claimed and walked separately by sibling scanners,
	// each producing their own stats).
	Stats WalkStats
}

// WalkShallow lists `root` non-recursively. Files and the root directory
// itself emit through fn. Subdirectories are NOT walked — their names
// are returned in ShallowResult.SubdirNames so the caller can split
// them off as separate work units for cooperating scanners to claim.
//
// Used by the unit-coordinated agent path (Phase 2 of v0.5.x parallel
// scanning) on the root unit ("") of a scan, to fan out top-level
// subtrees across siblings without one scanner walking the whole tree.
func WalkShallow(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fn WalkFunc,
) (ShallowResult, error) {
	res := ShallowResult{}
	if err := ctx.Err(); err != nil {
		return res, err
	}

	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	entries, err := os.ReadDir(root)
	if err != nil {
		// Same swallow behaviour as Walk — let an unreadable root
		// surface as zero entries rather than failing the whole scan.
		// Account for it so the api can surface the skip.
		res.Stats.InaccessibleDirs++
		return res, nil
	}

	owners := metadata.NewOwnerResolver()

	for _, d := range entries {
		name := d.Name()
		if excludeSet[strings.ToLower(name)] {
			continue
		}
		childPath := filepath.Join(root, name)
		if d.IsDir() {
			// Don't emit a record here; the unit walker for this
			// subdir will emit it post-order with its own subtree
			// totals when the sibling claims and walks it.
			res.SubdirNames = append(res.SubdirNames, name)
			continue
		}
		info, err := d.Info()
		if err != nil {
			res.Stats.InaccessibleFiles++
			continue
		}
		entry, err := metadata.CollectFromInfo(childPath, info, computeHash, owners)
		if err != nil {
			res.Stats.InaccessibleFiles++
			continue
		}
		if err := fn(entry); err != nil {
			return res, err
		}
	}
	return res, nil
}

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
// Errors from individual entries are accumulated in WalkStats but do
// not abort the walk — a single permission-denied subdirectory
// shouldn't kill the whole scan. ctx cancellation, however, is honored
// — the walk returns ctx.Err() at the next directory boundary so a
// SIGTERM / scan-cancel actually stops in-flight 10M+ scans.
func Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn WalkFunc) (WalkStats, error) {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	owners := metadata.NewOwnerResolver()

	var stats WalkStats
	// We don't emit the root itself — `walkDir` returns its totals to
	// nowhere. Real code only cares about descendants.
	_, err := walkDir(ctx, root, root, excludeSet, computeHash, owners, fn, &stats)
	return stats, err
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
	stats *WalkStats,
) (subtreeTotals, error) {
	var totals subtreeTotals

	if err := ctx.Err(); err != nil {
		return totals, err
	}

	entries, err := os.ReadDir(path)
	if err != nil {
		// Permission denied / race with deletion. Subtree totals stay
		// at zero and the scan continues; the api surfaces the skip
		// via the inaccessible_dirs count on the Scan row.
		stats.InaccessibleDirs++
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
			if d.IsDir() {
				stats.InaccessibleDirs++
			} else {
				stats.InaccessibleFiles++
			}
			continue
		}

		if d.IsDir() {
			// Recurse first, then emit the child directory's record
			// with its accumulated totals.
			childTotals, cerr := walkDir(ctx, childPath, root, excludeSet, computeHash, owners, fn, stats)
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
				stats.InaccessibleDirs++
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
				stats.InaccessibleFiles++
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
