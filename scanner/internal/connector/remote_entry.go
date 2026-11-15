package connector

import (
	"context"
	"fmt"
	"io/fs"
	"path/filepath"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// fileInfoToEntry converts an fs.FileInfo into an EntryRecord. Used by the
// remote connectors (smb) which can't capture POSIX uid/gid/ACL/xattr
// the way the local walker can — those fields are left empty.
//
// Mode is run through metadata.SafeMode so Go's high-bit flags
// (os.ModeDir, os.ModeSymlink, …) don't overflow the api side's INT32
// `mode` column — that's the bug that made every SMB batch ingest
// 500 with "value out of int32 range".
func fileInfoToEntry(ctx context.Context, path string, info fs.FileInfo, computeHash bool, conn Connector) *apiclient.EntryIn {
	modTime := info.ModTime()
	isDir := info.IsDir()
	entry := &apiclient.EntryIn{
		Path:         path,
		Name:         info.Name(),
		Kind:         wire.KindOf(isDir),
		Mode:         wire.To(int(metadata.SafeMode(info))),
		FsModifiedAt: &modTime,
	}
	if !isDir {
		entry.SizeBytes = wire.To(int(info.Size()))
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = wire.To(strings.TrimPrefix(ext, "."))
		}
	}

	if computeHash && !isDir && conn != nil {
		if rc, err := conn.ReadFile(ctx, path); err == nil {
			if hash, err := metadata.HashReader(rc); err == nil {
				entry.ContentHash = &hash
			}
			rc.Close()
		}
	}

	// Suppress "unused fmt" if downstream changes pull fmt back in.
	_ = fmt.Sprintf
	return entry
}
