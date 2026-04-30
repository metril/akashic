package connector

import (
	"context"
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fileInfoToEntry converts an fs.FileInfo into an EntryRecord. Used by the
// remote connectors (ssh, smb) which can't capture POSIX uid/gid/ACL/xattr
// the way the local walker can — those fields are left empty.
//
// Mode is run through metadata.SafeMode so Go's high-bit flags
// (os.ModeDir, os.ModeSymlink, …) don't overflow the api side's INT32
// `mode` column — that's the bug that made every SMB/SSH batch ingest
// 500 with "value out of int32 range".
func fileInfoToEntry(ctx context.Context, path string, info fs.FileInfo, computeHash bool, conn Connector) *models.EntryRecord {
	modTime := info.ModTime()
	mode := metadata.SafeMode(info)
	entry := &models.EntryRecord{
		Path:       path,
		Name:       info.Name(),
		Mode:       &mode,
		ModifiedAt: &modTime,
	}
	if info.IsDir() {
		entry.Kind = "directory"
	} else {
		entry.Kind = "file"
		size := info.Size()
		entry.SizeBytes = &size
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	if computeHash && entry.Kind == "file" && conn != nil {
		if rc, err := conn.ReadFile(ctx, path); err == nil {
			if hash, err := metadata.HashReader(rc); err == nil {
				entry.ContentHash = hash
			}
			rc.Close()
		}
	}

	// Suppress "unused fmt" if downstream changes pull fmt back in.
	_ = fmt.Sprintf
	return entry
}
