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
	"sync/atomic"
	"time"

	"github.com/hirochachacha/go-smb2"

	"github.com/akashic-project/akashic/scanner/internal/lsarpc"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
	"github.com/akashic-project/akashic/scanner/internal/walker"
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
	// v0.29.5 — explicit opt-in for empty-password sessions. Defaults
	// to false; Connect() refuses an empty password unless the caller
	// explicitly flips this. Lab/anonymous-share configs can set it
	// via SetAllowEmptyPassword.
	allowEmptyPassword bool
	conn      net.Conn
	session   *smb2.Session
	smbShare  *smb2.Share
	ipcShare  *smb2.Share
	lsaClient *lsarpc.Client
	resolver  *metadata.SIDResolver
	// sdSource provides raw security descriptor bytes for each path.
	// Populated from smbShare after Connect(); can be overridden in tests.
	sdSource  sdFetcher
	// v0.32.2 — guard support. ctx is the connect-time (scan) context
	// every guard() bounds its SMB op against; stalled latches once an
	// SMB op was force-aborted because the server stopped responding.
	ctx     context.Context
	stalled atomic.Bool
}

func NewSMBConnector(host string, port int, username, password, share string) *SMBConnector {
	return &SMBConnector{
		host:     host,
		port:     port,
		username: username,
		password: password,
		share:    share,
		ctx:      context.Background(),
	}
}

// smbOpTimeout bounds a single SMB operation. A healthy server answers
// in milliseconds; one that has stalled — the host slept, the share
// went offline, a network blip left a half-open TCP connection — would
// otherwise park the scan goroutine in a deadline-less socket read
// forever, since Go context cancellation cannot interrupt a syscall.
// Five minutes is far longer than any real round trip yet bounds a
// genuine stall. A var, not a const, so tests can shorten it. v0.32.2.
var smbOpTimeout = 300 * time.Second

// smbDialTimeout bounds the bare TCP dial in the LSA / SAMR RPC paths
// (smb_lsa.go, smb_samr.go) so a black-holed host can't wedge SID
// resolution before a connection is even established. v0.32.2.
const smbDialTimeout = 30 * time.Second

// guard runs one blocking SMB call with a timeout and context
// awareness. On timeout or context cancellation it force-closes the
// underlying TCP connection — that unblocks the wedged go-smb2 call
// (its socket read returns an error), so fn returns and its goroutine
// exits with no leak. The walk then unwinds normally instead of
// hanging until the scanner process is restarted.
//
// A socket-level deadline can't be used instead: go-smb2's receive
// loop sits permanently blocked reading the socket and tears the whole
// connection down on any read error, so a deadline would kill a
// healthy connection that merely idled. Bounding per-operation only
// trips when an SMB call is genuinely in flight.
func (c *SMBConnector) guard(name string, fn func() error) error {
	// ctx is set by Connect / NewSMBConnector; fall back to Background
	// for connectors built as a bare struct literal (e.g. unit tests).
	ctx := c.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	done := make(chan error, 1)
	go func() { done <- fn() }()
	select {
	case err := <-done:
		return err
	case <-ctx.Done():
		if c.conn != nil {
			_ = c.conn.Close()
		}
		return ctx.Err()
	case <-time.After(smbOpTimeout):
		c.stalled.Store(true)
		if c.conn != nil {
			_ = c.conn.Close()
		}
		return fmt.Errorf(
			"smb %s: server stalled — no response in %s; connection closed",
			name, smbOpTimeout,
		)
	}
}

// guardedReader wraps an SMB file handle so each Read/Close runs under
// guard. File-content reads stream chunk-by-chunk and may legitimately
// run long, so they can't be bounded as one fixed-timeout operation —
// per-Read guarding makes it an inactivity bound: a healthy stream
// refreshes the timer every chunk, a stalled one trips it.
type guardedReader struct {
	c  *SMBConnector
	rc io.ReadCloser
}

func (g *guardedReader) Read(p []byte) (int, error) {
	var n int
	err := g.c.guard("file read", func() error {
		var e error
		n, e = g.rc.Read(p)
		return e
	})
	return n, err
}

func (g *guardedReader) Close() error {
	return g.c.guard("file close", func() error { return g.rc.Close() })
}

// SetAllowEmptyPassword opts the connector into empty-password sessions
// (anonymous / null-password / guest-fallback configurations). Callers
// must set this BEFORE Connect — flipping it mid-session is a no-op.
//
// Default is false so the common-case mistake (credential profile
// missing the password) gets caught as `smb session: password required`
// rather than producing a green probe + ambiguous-data scan.
func (c *SMBConnector) SetAllowEmptyPassword(v bool) {
	c.allowEmptyPassword = v
}

func (c *SMBConnector) Connect(ctx context.Context) error {
	// v0.32.2 — every guard() call bounds its SMB op against this context.
	c.ctx = ctx
	// v0.29.5 — defense in depth against the empty-password bypass
	// (see scanner/internal/probe/probe.go:runSMB for the full
	// rationale). The probe rejects this case for reachability tests;
	// the connector enforces the same rule for real scans so a
	// credential row that slipped through API validation still gets
	// caught before producing ambiguous scan data.
	if c.password == "" && !c.allowEmptyPassword {
		return fmt.Errorf(
			"smb session: password required for user %q "+
				"(empty-password sessions disabled; "+
				"call SetAllowEmptyPassword(true) to opt in)",
			c.username,
		)
	}

	addr := net.JoinHostPort(c.host, fmt.Sprintf("%d", c.port))
	// DialContext (review S-C2): plain net.Dial has no timeout, so an
	// unreachable host (firewall drop, packet loss) blocks the goroutine
	// for the OS TCP timeout (minutes) — heartbeats keep the lease
	// alive throughout, so the scan stays stuck. The probe path passes
	// a bounded context; obeying it bounds the dial properly.
	dialer := net.Dialer{}
	conn, err := dialer.DialContext(ctx, "tcp", addr)
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

	var session *smb2.Session
	if err := c.guard("session setup", func() error {
		var e error
		session, e = d.Dial(conn)
		return e
	}); err != nil {
		conn.Close()
		return fmt.Errorf("smb session: %w", err)
	}

	// v0.29.1 — reject guest / anonymous downgrades. Windows and
	// Samba both honour a server-side "fall back to guest" policy:
	// when the NTLMSSP credentials don't match a known account, the
	// server returns a SUCCESSFUL session-setup with the IS_GUEST
	// (or IS_NULL) flag set instead of an auth failure. The pre-fix
	// SMB probe accepted that, so a user who supplied wrong
	// credentials saw "reachable" because the server happily handed
	// them a guest session. The user reported this as the original
	// bug. We mint these connections only with explicit credentials,
	// so a guest result means the server effectively rejected those
	// credentials — surface as an auth failure, not silent success.
	if session.IsGuest() {
		session.Logoff()
		conn.Close()
		return fmt.Errorf(
			"smb session: server fell back to guest session for user %q — "+
				"supplied credentials were rejected (configure the server to "+
				"deny guest fallback if you need to detect this earlier)",
			c.username,
		)
	}
	if session.IsAnonymous() {
		session.Logoff()
		conn.Close()
		return fmt.Errorf(
			"smb session: server returned an anonymous (NULL) session for "+
				"user %q — supplied credentials were rejected", c.username,
		)
	}
	c.session = session

	var share *smb2.Share
	if err := c.guard("mount", func() error {
		var e error
		share, e = session.Mount(c.share)
		return e
	}); err != nil {
		session.Logoff()
		conn.Close()
		return fmt.Errorf("smb mount %s: %w", c.share, err)
	}
	c.smbShare = share
	c.sdSource = share // *smb2.Share satisfies sdFetcher via GetSecurityDescriptorBytes

	// v0.29.6 — share-ACL smoke. v0.29.1 catches guest-fallback +
	// v0.29.5 catches empty-password sessions, but there's a third
	// bypass: the user authenticates fine, gets tree-connect
	// permission to mount the share, but ACL denies READ at the
	// share root. Pre-fix Connect returned nil here, the probe lands
	// ok=true, and at scan time the walker hits ACCESS_DENIED on
	// every ReadDir → swallows as inaccessible_dirs → zero entries
	// produced → final batch crash (or just a silent empty scan).
	//
	// Drop a tiny ReadDir(".") to confirm the user can actually
	// LIST the share before claiming success. Empty result is a
	// legit pass (mountable + readable + empty share is a real
	// configuration). go-smb2's Share.ReadDir reads relative to the
	// mount; "." is the share root.
	if rerr := c.guard("readdir smoke test", func() error {
		_, e := share.ReadDir(".")
		return e
	}); rerr != nil {
		share.Umount()
		session.Logoff()
		conn.Close()
		c.smbShare = nil
		c.sdSource = nil
		return fmt.Errorf(
			"smb session: share %q mounted but ReadDir denied "+
				"(credentials lack list permission for user %q: %s)",
			c.share, c.username, rerr,
		)
	}

	// Try opening LSARPC named pipe for SID resolution. Failures are non-fatal —
	// capture continues with raw SIDs (well-known table still resolves what it can).
	// go-smb2 requires a separate IPC$ mount to access named pipes; keep ipcShare
	// alive for the duration so the underlying tree connection stays open.
	// Wrapped in one guard: a server that stalls during this best-effort
	// LSA setup must not wedge Connect. On timeout guard closes the
	// connection; the first walk op then fails fast and the scan unwinds.
	_ = c.guard("lsa setup", func() error {
		ipcShare, ipcErr := c.session.Mount(fmt.Sprintf(`\\%s\IPC$`, c.host))
		if ipcErr != nil {
			return nil
		}
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
		return nil
	})
	c.resolver = metadata.NewSIDResolver(lsaAdapter{c.lsaClient})

	return nil
}

func (c *SMBConnector) Walk(ctx context.Context, root string, excludePatterns []string, computeHash bool, _ bool, fn func(*models.EntryRecord) error) (walker.WalkStats, error) {
	if c.smbShare == nil {
		return walker.WalkStats{}, fmt.Errorf("not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	var stats walker.WalkStats
	err := c.walkDir(ctx, root, excludeSet, computeHash, fn, &stats)
	return stats, err
}

func (c *SMBConnector) walkDir(ctx context.Context, dir string, excludeSet map[string]bool, computeHash bool, fn func(*models.EntryRecord) error, stats *walker.WalkStats) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var entries []os.FileInfo
	err := c.guard("readdir "+dir, func() error {
		var e error
		entries, e = c.smbShare.ReadDir(dir)
		return e
	})
	if err != nil {
		// A stalled connection (guard force-closed it) means the rest of
		// this subtree can't be walked — abort instead of silently
		// shipping a partial scan. A non-stall error is a per-directory
		// permission/transient issue: count it and move on.
		if c.stalled.Load() {
			return fmt.Errorf("smb walk aborted at %q: %w", dir, err)
		}
		stats.InaccessibleDirs++
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
			if err := c.walkDir(ctx, path, excludeSet, computeHash, fn, stats); err != nil {
				return err
			}
		}
	}
	return nil
}

// WalkShallow implements connector.ShallowWalker.
//
// Lists immediate children of `root` via SMB ReadDir without
// recursing. Files emit through fn (with hashing + SD capture
// preserved); subdirectory names are returned for the caller to
// split off as work units.
func (c *SMBConnector) WalkShallow(
	ctx context.Context, root string, excludePatterns []string,
	computeHash bool, fn func(*models.EntryRecord) error,
) ([]string, error) {
	if c.smbShare == nil {
		return nil, fmt.Errorf("not connected")
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}
	var infos []os.FileInfo
	if err := c.guard("readdir "+root, func() error {
		var e error
		infos, e = c.smbShare.ReadDir(root)
		return e
	}); err != nil {
		// Propagate so the unit runner sees a real failure. Returning
		// (nil, nil) here used to silently mask permission/connectivity
		// failures at the share root and ship a zero-subdirectory plan,
		// dropping every subtree from the scan with no surfaced error.
		return nil, fmt.Errorf("smb: readdir %q: %w", root, err)
	}
	var subdirs []string
	for _, info := range infos {
		name := info.Name()
		if excludeSet[strings.ToLower(name)] {
			continue
		}
		if info.IsDir() {
			subdirs = append(subdirs, name)
			continue
		}
		p := filepath.Join(root, name)
		entry := fileInfoToEntry(ctx, p, info, false, nil)
		if computeHash {
			if hash, herr := c.hashRemoteFile(p); herr == nil {
				entry.ContentHash = hash
			}
		}
		if sd, sderr := c.querySecurityDescriptor(p); sderr == nil && len(sd) > 0 {
			if acl, aerr := metadata.SDToNtACL(sd, c.resolver); aerr == nil {
				entry.Acl = acl
			}
		}
		if err := fn(entry); err != nil {
			return subdirs, err
		}
	}
	return subdirs, nil
}

func (c *SMBConnector) hashRemoteFile(path string) (string, error) {
	var f *smb2.File
	if err := c.guard("open "+path, func() error {
		var e error
		f, e = c.smbShare.Open(path)
		return e
	}); err != nil {
		return "", err
	}
	gr := &guardedReader{c: c, rc: f}
	defer gr.Close()
	return metadata.HashReader(gr)
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
	var sd []byte
	err := c.guard("security descriptor "+path, func() error {
		var e error
		sd, e = c.sdSource.GetSecurityDescriptorBytes(path)
		return e
	})
	return sd, err
}

func (c *SMBConnector) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if c.smbShare == nil {
		return nil, fmt.Errorf("not connected")
	}
	var f *smb2.File
	if err := c.guard("open "+path, func() error {
		var e error
		f, e = c.smbShare.Open(path)
		return e
	}); err != nil {
		return nil, err
	}
	return &guardedReader{c: c, rc: f}, nil
}

// Delete removes a file from the SMB share. The bound user needs the
// DELETE access right on the file (mapped from the NT ACL). go-smb2
// surfaces permission failures as smb-status-code wrapped errors —
// callers see them verbatim as the "step:reason" message.
func (c *SMBConnector) Delete(_ context.Context, path string) error {
	if c.smbShare == nil {
		return fmt.Errorf("not connected")
	}
	var st os.FileInfo
	if err := c.guard("stat "+path, func() error {
		var e error
		st, e = c.smbShare.Stat(path)
		return e
	}); err != nil {
		return err
	}
	if st.IsDir() {
		return fmt.Errorf("refusing to delete directory %q", path)
	}
	return c.guard("remove "+path, func() error {
		return c.smbShare.Remove(path)
	})
}

func (c *SMBConnector) Close() error {
	if c.lsaClient != nil {
		_ = c.lsaClient.Close()
	}
	// Bound the teardown: Umount/Logoff are SMB round trips, so a server
	// that has gone away would otherwise wedge Close — the same hang as
	// the walk path. guard force-closes c.conn on timeout (or at once if
	// the scan context is already cancelled), so Close always returns.
	if c.ipcShare != nil {
		_ = c.guard("ipc umount", func() error { return c.ipcShare.Umount() })
	}
	if c.smbShare != nil {
		_ = c.guard("umount", func() error { return c.smbShare.Umount() })
	}
	if c.session != nil {
		_ = c.guard("logoff", func() error { return c.session.Logoff() })
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
