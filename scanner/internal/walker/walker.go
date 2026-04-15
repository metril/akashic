package walker

import (
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type WalkFunc func(entry *models.FileEntry) error

func Walk(root string, excludePatterns []string, computeHash bool, fn WalkFunc) error {
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	return filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
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

		info, err := d.Info()
		if err != nil {
			return nil
		}

		entry, err := metadata.CollectFromInfo(path, info, computeHash)
		if err != nil {
			return nil
		}

		return fn(entry)
	})
}
