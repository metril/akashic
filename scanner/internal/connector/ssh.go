package connector

import (
	"context"
	"fmt"
	"io"
	"io/fs"
	"log"
	"net"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
	"github.com/pkg/sftp"
	gossh "golang.org/x/crypto/ssh"
	"golang.org/x/crypto/ssh/knownhosts"
)

// SSHConnector connects to a remote host via SSH/SFTP and walks the filesystem.
type SSHConnector struct {
	host           string
	port           int
	username       string
	password       string
	keyPath        string
	keyPassphrase  string
	knownHostsPath string

	sshClient  *gossh.Client
	sftpClient *sftp.Client
}

// NewSSHConnector creates a new SSHConnector.
func NewSSHConnector(host string, port int, username, password, keyPath, keyPassphrase, knownHostsPath string) *SSHConnector {
	return &SSHConnector{
		host:           host,
		port:           port,
		username:       username,
		password:       password,
		keyPath:        keyPath,
		keyPassphrase:  keyPassphrase,
		knownHostsPath: knownHostsPath,
	}
}

// Connect dials the remote SSH server and creates an SFTP session.
func (c *SSHConnector) Connect(_ context.Context) error {
	authMethods := []gossh.AuthMethod{}

	if c.keyPath != "" {
		key, err := os.ReadFile(c.keyPath)
		if err != nil {
			return fmt.Errorf("read ssh key: %w", err)
		}
		var signer gossh.Signer
		if c.keyPassphrase != "" {
			signer, err = gossh.ParsePrivateKeyWithPassphrase(key, []byte(c.keyPassphrase))
		} else {
			signer, err = gossh.ParsePrivateKey(key)
		}
		if err != nil {
			return fmt.Errorf("parse ssh key: %w", err)
		}
		authMethods = append(authMethods, gossh.PublicKeys(signer))
	}

	if c.password != "" {
		authMethods = append(authMethods, gossh.Password(c.password))
	}

	var hostKeyCallback gossh.HostKeyCallback
	if c.knownHostsPath != "" {
		cb, err := knownhosts.New(c.knownHostsPath)
		if err != nil {
			return fmt.Errorf("load known_hosts %s: %w", c.knownHostsPath, err)
		}
		hostKeyCallback = cb
	} else {
		log.Printf("warning: SSH host key verification disabled (no --known-hosts provided)")
		hostKeyCallback = gossh.InsecureIgnoreHostKey() //nolint:gosec
	}

	cfg := &gossh.ClientConfig{
		User:            c.username,
		Auth:            authMethods,
		HostKeyCallback: hostKeyCallback,
		Timeout:         15 * time.Second,
	}

	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	sshClient, err := gossh.Dial("tcp", addr, cfg)
	if err != nil {
		return fmt.Errorf("ssh dial %s: %w", addr, err)
	}
	c.sshClient = sshClient

	sftpClient, err := sftp.NewClient(sshClient)
	if err != nil {
		sshClient.Close()
		return fmt.Errorf("sftp client: %w", err)
	}
	c.sftpClient = sftpClient

	return nil
}

// Walk traverses the remote filesystem starting at root via SFTP.
func (c *SSHConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fn func(*models.FileEntry) error) error {
	if c.sftpClient == nil {
		return fmt.Errorf("not connected")
	}

	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	walker := c.sftpClient.Walk(root)
	for walker.Step() {
		if err := walker.Err(); err != nil {
			log.Printf("warning: walk error at %s: %v", walker.Path(), err)
			continue
		}

		path := walker.Path()
		stat := walker.Stat()
		name := stat.Name()

		if path == root {
			continue
		}

		if excludeSet[strings.ToLower(name)] {
			if stat.IsDir() {
				walker.SkipDir()
			}
			continue
		}

		entry := fileInfoToEntry(ctx, path, stat, computeHash, c)
		if err := fn(entry); err != nil {
			return err
		}
	}

	return nil
}

// ReadFile opens a remote file for reading via SFTP.
func (c *SSHConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.sftpClient == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.sftpClient.Open(path)
}

// Close shuts down the SFTP and SSH connections.
func (c *SSHConnector) Close() error {
	var firstErr error
	if c.sftpClient != nil {
		if err := c.sftpClient.Close(); err != nil {
			firstErr = err
		}
	}
	if c.sshClient != nil {
		if err := c.sshClient.Close(); err != nil && firstErr == nil {
			firstErr = err
		}
	}
	return firstErr
}

// Type returns the connector type.
func (c *SSHConnector) Type() string {
	return "ssh"
}

// fileInfoToEntry converts an fs.FileInfo into a models.FileEntry.
// If computeHash is true and the file is not a directory, it reads from
// the connector to compute the content hash.
func fileInfoToEntry(ctx context.Context, path string, info fs.FileInfo, computeHash bool, conn Connector) *models.FileEntry {
	modTime := info.ModTime()
	entry := &models.FileEntry{
		Path:       path,
		Filename:   info.Name(),
		Extension:  strings.TrimPrefix(filepath.Ext(info.Name()), "."),
		SizeBytes:  info.Size(),
		Permissions: fmt.Sprintf("%o", info.Mode().Perm()),
		ModifiedAt: &modTime,
		IsDir:      info.IsDir(),
	}

	if computeHash && !info.IsDir() {
		if conn != nil {
			if rc, err := conn.ReadFile(ctx, path); err == nil {
				if hash, err := metadata.HashReader(rc); err == nil {
					entry.ContentHash = hash
				}
				rc.Close()
			}
		}
	}

	return entry
}
