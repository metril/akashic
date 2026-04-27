package connector

import (
	"context"
	"encoding/binary"
	"errors"
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

// querySecurityDescriptor returns the raw NT security descriptor bytes for the path.
//
// NOTE: The pinned hirochachacha/go-smb2 release does not expose the
// SMB2 QUERY_INFO request needed to fetch security descriptors. This stub
// returns an "unavailable" sentinel; NT ACL capture is therefore disabled
// until the dependency exposes the API or we drop to a forked smb2 client.
func (c *SMBConnector) querySecurityDescriptor(path string) ([]byte, error) {
	return nil, errSMBSecurityUnavailable
}

var errSMBSecurityUnavailable = errors.New("smb security capture unavailable: go-smb2 needs to expose GetSecurityDescriptor")

func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.smbShare == nil {
		return nil, fmt.Errorf("not connected")
	}
	return c.smbShare.Open(path)
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
