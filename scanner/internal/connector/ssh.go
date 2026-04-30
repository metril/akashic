package connector

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"path"
	"strings"
	"time"

	"github.com/pkg/sftp"
	gossh "golang.org/x/crypto/ssh"
	"golang.org/x/crypto/ssh/knownhosts"

	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
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

	hasGetfacl     bool
	hasNfs4Getfacl bool
	aclCache       map[string]*models.ACL // keyed by absolute path
	aclMode        string                 // "full" | "perdir"
}

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

	c.hasGetfacl = c.remoteHas("getfacl")
	c.hasNfs4Getfacl = c.remoteHas("nfs4_getfacl")
	if !c.hasGetfacl && !c.hasNfs4Getfacl {
		log.Printf("ssh: neither getfacl nor nfs4_getfacl available on %s — ACL capture disabled", c.host)
	}
	c.aclCache = make(map[string]*models.ACL)

	return nil
}

func (c *SSHConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, fullScan bool, fn func(*models.EntryRecord) error) error {
	if c.sftpClient == nil {
		return fmt.Errorf("not connected")
	}

	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	// Mode selection: full scans get a single full-tree dump.
	c.aclCache = make(map[string]*models.ACL)
	if fullScan {
		c.aclMode = "full"
		c.prefetchACLs(root, true)
	} else {
		c.aclMode = "perdir"
	}

	walker := c.sftpClient.Walk(root)
	currentDir := ""
	for walker.Step() {
		if err := walker.Err(); err != nil {
			log.Printf("warning: walk error at %s: %v", walker.Path(), err)
			continue
		}

		p := walker.Path()
		stat := walker.Stat()
		name := stat.Name()

		if p == root {
			continue
		}

		if excludeSet[strings.ToLower(name)] {
			if stat.IsDir() {
				walker.SkipDir()
			}
			continue
		}

		if c.aclMode == "perdir" {
			parent := path.Dir(p)
			if parent != currentDir {
				currentDir = parent
				c.prefetchACLs(parent, false)
			}
		}

		entry := fileInfoToEntry(ctx, p, stat, computeHash, c)
		if acl, ok := c.aclCache[p]; ok {
			entry.Acl = acl
		}
		if err := fn(entry); err != nil {
			return err
		}
	}

	return nil
}

func (c *SSHConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.sftpClient == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.sftpClient.Open(path)
}

// Delete removes a file via SFTP. The pkg/sftp client distinguishes
// directories from files — Lstat first so we never mistakenly call
// Remove on a directory (which the server would reject anyway, but the
// error is more legible if we own the rejection).
func (c *SSHConnector) Delete(_ context.Context, path string) error {
	if c.sftpClient == nil {
		return fmt.Errorf("not connected")
	}
	st, err := c.sftpClient.Lstat(path)
	if err != nil {
		return err
	}
	if st.IsDir() {
		return fmt.Errorf("refusing to delete directory %q", path)
	}
	return c.sftpClient.Remove(path)
}

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

func (c *SSHConnector) Type() string {
	return "ssh"
}

func (c *SSHConnector) remoteHas(tool string) bool {
	out, err := c.runRemote("command -v " + tool + " >/dev/null 2>&1 && echo yes || echo no")
	if err != nil {
		return false
	}
	return strings.TrimSpace(out) == "yes"
}

func (c *SSHConnector) runRemote(cmd string) (string, error) {
	sess, err := c.sshClient.NewSession()
	if err != nil {
		return "", err
	}
	defer sess.Close()
	var stdout, stderr bytes.Buffer
	sess.Stdout = &stdout
	sess.Stderr = &stderr
	if err := sess.Run(cmd); err != nil {
		return stdout.String(), fmt.Errorf("ssh exec %q: %w (stderr=%s)", cmd, err, stderr.String())
	}
	return stdout.String(), nil
}

// prefetchACLs runs a remote dump command and merges results into c.aclCache.
func (c *SSHConnector) prefetchACLs(scope string, fullTree bool) {
	if !c.hasGetfacl && !c.hasNfs4Getfacl {
		return
	}
	depth := ""
	if !fullTree {
		depth = "-maxdepth 1 -mindepth 1"
	}
	scopeQ := shellQuote(scope)

	if c.hasNfs4Getfacl {
		cmd := fmt.Sprintf(
			`find %s %s -print0 2>/dev/null | xargs -0 -I{} sh -c 'echo "# file: $1"; nfs4_getfacl "$1" 2>/dev/null; echo' _ {}`,
			scopeQ, depth,
		)
		if out, err := c.runRemote(cmd); err == nil {
			for k, v := range metadata.ParseRemoteNfs4Dump(out) {
				c.aclCache[k] = v
			}
		}
	}
	if c.hasGetfacl {
		cmd := fmt.Sprintf(
			`find %s %s -exec getfacl --absolute-names {} + 2>/dev/null`,
			scopeQ, depth,
		)
		if out, err := c.runRemote(cmd); err == nil {
			for k, v := range metadata.ParseRemotePosixDump(out) {
				if _, alreadyHaveNfs4 := c.aclCache[k]; !alreadyHaveNfs4 {
					c.aclCache[k] = v
				}
			}
		}
	}
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
