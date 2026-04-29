package walker

import (
	"io/fs"
	"path/filepath"
	"strings"
)

// PrewalkResult is the count-only summary the prewalk produces. The
// walker reports it once at the end of the prewalk pass; the API records
// it as `total_estimated` on the Scan so the UI can show a real ETA
// during the subsequent real walk.
type PrewalkResult struct {
	Files int64
	Dirs  int64
	Bytes int64
}

// PrewalkProgress is invoked every N entries during the prewalk so the
// scanner can update the heartbeat counters. nil disables progress
// reporting (used in tests).
type PrewalkProgress func(filesSoFar, dirsSoFar, bytesSoFar int64, currentPath string)

// Prewalk walks `root` collecting only counts — no hashing, no metadata
// reads beyond the os.FileInfo that filepath.WalkDir already provides.
// Significantly cheaper than the real Walk because we skip:
//
//   - per-entry getfacl / xattr / lsattr calls
//   - hash computation
//   - extended POSIX bits (mode is read but only to identify dirs)
//
// Same exclude-pattern semantics as Walk so the prewalk total matches
// the real walk's domain.
//
// `progressEvery` controls how often `progress` is invoked: every N
// entries (file or directory). 0 = never call progress.
func Prewalk(root string, excludePatterns []string, progress PrewalkProgress, progressEvery int) (PrewalkResult, error) {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	var (
		out   PrewalkResult
		count int
	)
	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if path == root {
			return nil
		}
		name := d.Name()
		if excludeSet[strings.ToLower(name)] {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}

		if d.IsDir() {
			out.Dirs++
		} else {
			out.Files++
			info, infoErr := d.Info()
			if infoErr == nil && info.Mode().IsRegular() {
				out.Bytes += info.Size()
			}
		}

		count++
		if progress != nil && progressEvery > 0 && count%progressEvery == 0 {
			progress(out.Files, out.Dirs, out.Bytes, path)
		}
		return nil
	})

	if progress != nil {
		// Final tick so the heartbeat sees the terminal total.
		progress(out.Files, out.Dirs, out.Bytes, "")
	}
	return out, err
}
