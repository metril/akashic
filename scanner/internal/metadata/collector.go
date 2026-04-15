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

func Collect(path string, computeHash bool) (*models.FileEntry, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return nil, fmt.Errorf("stat %s: %w", path, err)
	}

	entry := &models.FileEntry{
		Path:      path,
		Filename:  info.Name(),
		SizeBytes: info.Size(),
		IsDir:     info.IsDir(),
	}

	if !info.IsDir() {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	entry.Permissions = info.Mode().Perm().String()
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		entry.Owner = fmt.Sprintf("%d", stat.Uid)
		entry.Group = fmt.Sprintf("%d", stat.Gid)
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.AccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.CreatedAt = &ctime
	}

	if !info.IsDir() {
		entry.MimeType = detectMIME(path)
	}

	if computeHash && !info.IsDir() {
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

func CollectFromInfo(path string, info fs.FileInfo, computeHash bool) (*models.FileEntry, error) {
	entry := &models.FileEntry{
		Path:      path,
		Filename:  info.Name(),
		SizeBytes: info.Size(),
		IsDir:     info.IsDir(),
	}

	if !info.IsDir() {
		ext := filepath.Ext(info.Name())
		if ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
	}

	entry.Permissions = info.Mode().Perm().String()
	modTime := info.ModTime()
	entry.ModifiedAt = &modTime

	if stat, ok := info.Sys().(*syscall.Stat_t); ok {
		entry.Owner = fmt.Sprintf("%d", stat.Uid)
		entry.Group = fmt.Sprintf("%d", stat.Gid)
		atime := time.Unix(stat.Atim.Sec, stat.Atim.Nsec)
		entry.AccessedAt = &atime
		ctime := time.Unix(stat.Ctim.Sec, stat.Ctim.Nsec)
		entry.CreatedAt = &ctime
	}

	if !info.IsDir() {
		entry.MimeType = detectMIME(path)
	}

	if computeHash && !info.IsDir() {
		hash, err := hashFile(path)
		if err != nil {
			return nil, err
		}
		entry.ContentHash = hash
	}

	return entry, nil
}
