package connector

import (
	"context"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/hirochachacha/go-smb2"

	"github.com/akashic-project/akashic/scanner/internal/lsarpc"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// sdFetcher is the narrow interface for fetching raw NT security descriptor
// bytes. *smb2.Share (from the vendored go-smb2) satisfies it automatically.
// The interface exists solely so unit tests can inject a mock without a live
// SMB server.
type sdFetcher interface {
	GetSecurityDescriptorBytes(path string) ([]byte, error)
}

type SMBConnector struct {
	host      string
	port      int
	username  string
	password  string
	share     string
	conn      net.Conn
	session   *smb2.Session
	smbShare  *smb2.Share
	ipcShare  *smb2.Share
	lsaClient *lsarpc.Client
	resolver  *metadata.SIDResolver
	// sdSource provides raw security descriptor bytes for each path.
	// Populated from smbShare after Connect(); can be overridden in tests.
	sdSource  sdFetcher
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
	c.sdSource = share // *smb2.Share satisfies sdFetcher via GetSecurityDescriptorBytes

	// Try opening LSARPC named pipe for SID resolution. Failures are non-fatal —
	// capture continues with raw SIDs (well-known table still resolves what it can).
	// go-smb2 requires a separate IPC$ mount to access named pipes; keep ipcShare
	// alive for the duration so the underlying tree connection stays open.
	if ipcShare, ipcErr := c.session.Mount(fmt.Sprintf(`\\%s\IPC$`, c.host)); ipcErr == nil {
		c.ipcShare = ipcShare
		if pipe, perr := ipcShare.OpenFile("lsarpc", os.O_RDWR, 0); perr == nil {
			client := lsarpc.NewClient(pipe)
			if berr := client.Bind(); berr == nil {
				if oerr := client.Open(); oerr == nil {
					c.lsaClient = client
				} else {
					_ = client.Close()
				}
			} else {
				_ = client.Close()
			}
		}
	}
	c.resolver = metadata.NewSIDResolver(lsaAdapter{c.lsaClient})

	return nil
}

func (c *SMBConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, _ bool, fn func(*models.EntryRecord) error) error {
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

		if sd, sderr := c.querySecurityDescriptor(path); sderr == nil && len(sd) > 0 {
			if acl, aerr := metadata.SDToNtACL(sd, c.resolver); aerr == nil {
				entry.Acl = acl
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

// querySecurityDescriptor returns the raw NT security descriptor bytes for
// the path via SMB2 QUERY_INFO (InfoType=SMB2_0_INFO_SECURITY,
// AdditionalInformation=OWNER|GROUP|DACL = 0x7, per MS-SMB2 §2.2.37).
//
// Implementation note — vendored go-smb2 patch
// ─────────────────────────────────────────────
// The stock hirochachacha/go-smb2 v1.1.0 does not expose the QUERY_INFO
// request needed to retrieve a security descriptor. We vendor a minimal patch
// at scanner/internal/vendor/go-smb2 that adds GetSecurityDescriptorBytes()
// on *smb2.Share — the only change relative to v1.1.0. The scanner's go.mod
// redirects the module via a replace directive.
//
// Upstream PR that inspired the patch:
//   https://github.com/hirochachacha/go-smb2/pull/65 (elimity-com, open as of 2026-04)
//
// To drop the vendor copy:
//   1. Wait for upstream to merge & tag a release with GetSecurityDescriptorBytes
//      (or equivalent raw-bytes API).
//   2. Remove the replace directive from scanner/go.mod.
//   3. Delete scanner/internal/vendor/go-smb2/.
func (c *SMBConnector) querySecurityDescriptor(path string) ([]byte, error) {
	if c.sdSource == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.sdSource.GetSecurityDescriptorBytes(path)
}

func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.smbShare == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.smbShare.Open(path)
}

// Delete removes a file from the SMB share. The bound user needs the
// DELETE access right on the file (mapped from the NT ACL). go-smb2
// surfaces permission failures as smb-status-code wrapped errors —
// callers see them verbatim as the "step:reason" message.
func (c *SMBConnector) Delete(_ context.Context, path string) error {
	if c.smbShare == nil {
		return fmt.Errorf("not connected")
	}
	st, err := c.smbShare.Stat(path)
	if err != nil {
		return err
	}
	if st.IsDir() {
		return fmt.Errorf("refusing to delete directory %q", path)
	}
	return c.smbShare.Remove(path)
}

func (c *SMBConnector) Close() error {
	if c.lsaClient != nil {
		_ = c.lsaClient.Close()
	}
	if c.ipcShare != nil {
		c.ipcShare.Umount()
	}
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

// lsaAdapter wraps *lsarpc.Client to satisfy metadata.SIDLookuper.
type lsaAdapter struct{ c *lsarpc.Client }

func (a lsaAdapter) Lookup(sid string) string {
	if a.c == nil {
		return ""
	}
	binSID := sidStringToBytes(sid)
	if binSID == nil {
		return ""
	}
	names, err := a.c.Lookup([][]byte{binSID})
	if err != nil || len(names) == 0 {
		return ""
	}
	return names[0].Name
}

func sidStringToBytes(s string) []byte {
	parts := strings.Split(s, "-")
	if len(parts) < 3 || parts[0] != "S" {
		return nil
	}
	auth, err := strconv.ParseUint(parts[2], 10, 64)
	if err != nil {
		return nil
	}
	subs := parts[3:]
	out := make([]byte, 8+len(subs)*4)
	out[0] = 1
	out[1] = byte(len(subs))
	for i := 5; i >= 0; i-- {
		out[2+i] = byte(auth & 0xff)
		auth >>= 8
	}
	for i, sv := range subs {
		v, perr := strconv.ParseUint(sv, 10, 32)
		if perr != nil {
			return nil
		}
		binary.LittleEndian.PutUint32(out[8+i*4:], uint32(v))
	}
	return out
}

func (c *SMBConnector) Type() string {
	return "smb"
}
