package connector

import (
	"context"
	"fmt"
	"io"
	"net"
	"path/filepath"
	"strings"

	"github.com/hirochachacha/go-smb2"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

type SMBConnector struct {
	host     string
	port     int
	username string
	password string
	share    string
	conn     net.Conn
	session  *smb2.Session
	smbShare *smb2.Share
}

func NewSMBConnector(host string, port int, username, password, share string) *SMBConnector {
	return &SMBConnector{
		host:     host,
		port:     port,
		username: username,
		password: password,
		share:    share,
	}
}

func (c *SMBConnector) Connect(_ context.Context) error {
	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return fmt.Errorf("smb dial %s: %w", addr, err)
	}
	c.conn = conn

	d := &smb2.Dialer{
		Initiator: &smb2.NTLMInitiator{
			User:     c.username,
			Password: c.password,
		},
	}

	session, err := d.Dial(conn)
	if err != nil {
		conn.Close()
		return fmt.Errorf("smb session: %w", err)
	}
	c.session = session

	share, err := session.Mount(c.share)
	if err != nil {
		session.Logoff()
		conn.Close()
		return fmt.Errorf("smb mount %s: %w", c.share, err)
	}
	c.smbShare = share

	return nil
}

func (c *SMBConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.EntryRecord) error) error {
	if c.smbShare == nil {
		return fmt.Errorf("not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	return c.walkDir(ctx, root, excludeSet, computeHash, fn)
}

func (c *SMBConnector) walkDir(ctx context.Context, dir string, excludeSet map[string]bool, computeHash bool, fn func(*models.EntryRecord) error) error {
	entries, err := c.smbShare.ReadDir(dir)
	if err != nil {
		return nil
	}

	for _, info := range entries {
		name := info.Name()
		if excludeSet[strings.ToLower(name)] {
			continue
		}

		path := filepath.Join(dir, name)
		entry := fileInfoToEntry(ctx, path, info, false, nil)

		if computeHash && !info.IsDir() {
			if hash, err := c.hashRemoteFile(path); err == nil {
				entry.ContentHash = hash
			}
		}

		if err := fn(entry); err != nil {
			return err
		}

		if info.IsDir() {
			if err := c.walkDir(ctx, path, excludeSet, computeHash, fn); err != nil {
				return err
			}
		}
	}
	return nil
}

func (c *SMBConnector) hashRemoteFile(path string) (string, error) {
	f, err := c.smbShare.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	return metadata.HashReader(f)
}

func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.smbShare == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.smbShare.Open(path)
}

func (c *SMBConnector) Close() error {
	if c.smbShare != nil {
		c.smbShare.Umount()
	}
	if c.session != nil {
		c.session.Logoff()
	}
	if c.conn != nil {
		c.conn.Close()
	}
	return nil
}

func (c *SMBConnector) Type() string {
	return "smb"
}
