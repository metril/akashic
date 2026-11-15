package metadata

import (
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/zeebo/blake3"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
)

// Collect builds an EntryRecord for `path` by calling Lstat. Use this when you
// don't already have a fs.FileInfo (for example when revisiting a path).
//
// The OwnerResolver may be nil; ACL/xattr capture happens unconditionally.
func Collect(path string, computeHash bool, owners *OwnerResolver) (*apiclient.EntryIn, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", path, err)
	}
	return CollectFromInfo(path, info, computeHash, owners)
}

// CollectFromInfo builds an EntryRecord from an existing fs.FileInfo. Used by
// the walker which already has DirEntry/Info from filepath.WalkDir.
func CollectFromInfo(path string, info fs.FileInfo, computeHash bool, owners *OwnerResolver) (*apiclient.EntryIn, error) {
	isDir := info.IsDir()
	entry := &apiclient.EntryIn{
		Path: path,
		Name: info.Name(),
		Kind: wire.KindOf(isDir),
	}

	if !isDir {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = wire.To(strings.TrimPrefix(ext, "."))
		}
		entry.SizeBytes = wire.To(int(info.Size()))
	}

	modTime := info.ModTime()
	entry.FsModifiedAt = &modTime

	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		// Use the native st_mode (16-bit POSIX value) rather than
		// Go's os.FileMode. Go's FileMode shoves classification flags
		// like os.ModeDir (1<<31) into the high bits, producing values
		// >2^31 that overflow the API's INT32 mode column. The native
		// st_mode is what users see in `stat -c %f` and what fits the
		// schema.
		entry.Mode = wire.To(int(uint32(stat.Mode)))
		entry.Uid = wire.To(int(stat.Uid))
		entry.Gid = wire.To(int(stat.Gid))
		if owners != nil {
			entry.OwnerName = wire.Str(owners.User(stat.Uid))
			entry.GroupName = wire.Str(owners.Group(stat.Gid))
		}
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.FsAccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.FsCreatedAt = &ctime
	} else {
		// Non-Linux fallback (tests on macOS/Windows, or remote
		// connectors that don't expose Stat_t). SafeMode masks Go's
		// high-bit flags down to the 12 POSIX bits so we don't ship a
		// value that overflows INT32 on the api side.
		entry.Mode = wire.To(int(SafeMode(info)))
	}

	if !isDir {
		entry.MimeType = wire.To(detectMIME(path))
	}

	if acl, err := CollectACL(path); err == nil && acl != nil {
		entry.Acl = acl
	}
	if xattrs, err := CollectXattrs(path); err == nil && xattrs != nil {
		entry.Xattrs = &xattrs
	}

	if computeHash && !isDir {
		hash, err := hashFile(path)
		if err != nil {
			return nil, fmt.Errorf("hash %s: %w", path, err)
		}
		entry.ContentHash = &hash
	}

	return entry, nil
}

func detectMIME(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return "application/octet-stream"
	}
	defer f.Close()

	buf := make([]byte, 512)
	n, err := f.Read(buf)
	if err != nil && err != io.EOF {
		return "application/octet-stream"
	}
	return http.DetectContentType(buf[:n])
}

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()

	hasher := blake3.New()
	if _, err := io.Copy(hasher, f); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hasher.Sum(nil)), nil
}

func HashReader(r io.Reader) (string, error) {
	hasher := blake3.New()
	if _, err := io.Copy(hasher, r); err != nil {
		return "", err
	}
	return fmt.Sprintf("%x", hasher.Sum(nil)), nil
}
