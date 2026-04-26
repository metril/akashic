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

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// Collect builds an EntryRecord for `path` by calling Lstat. Use this when you
// don't already have a fs.FileInfo (for example when revisiting a path).
//
// The OwnerResolver may be nil; ACL/xattr capture happens unconditionally.
func Collect(path string, computeHash bool, owners *OwnerResolver) (*models.EntryRecord, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", path, err)
	}
	return CollectFromInfo(path, info, computeHash, owners)
}

// CollectFromInfo builds an EntryRecord from an existing fs.FileInfo. Used by
// the walker which already has DirEntry/Info from filepath.WalkDir.
func CollectFromInfo(path string, info fs.FileInfo, computeHash bool, owners *OwnerResolver) (*models.EntryRecord, error) {
	entry := &models.EntryRecord{
		Path: path,
		Name: info.Name(),
	}
	if info.IsDir() {
		entry.Kind = "directory"
	} else {
		entry.Kind = "file"
	}

	if entry.Kind == "file" {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
		size := info.Size()
		entry.SizeBytes = &size
	}

	mode := uint32(info.Mode())
	entry.Mode = &mode
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		uid := stat.Uid
		gid := stat.Gid
		entry.Uid = &uid
		entry.Gid = &gid
		if owners != nil {
			entry.OwnerName = owners.User(uid)
			entry.GroupName = owners.Group(gid)
		}
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.AccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.CreatedAt = &ctime
	}

	if entry.Kind == "file" {
		entry.MimeType = detectMIME(path)
	}

	if acl, err := CollectACL(path); err == nil && acl != nil {
		entry.Acl = acl
	}
	if xattrs, err := CollectXattrs(path); err == nil && xattrs != nil {
		entry.Xattrs = xattrs
	}

	if computeHash && entry.Kind == "file" {
		hash, err := hashFile(path)
		if err != nil {
			return nil, fmt.Errorf("hash %s: %w", path, err)
		}
		entry.ContentHash = hash
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
