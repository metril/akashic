package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/nfsprobe"
)

// runTestConnection handles the `test-connection` subcommand. It performs a
// minimal probe (dial → auth → list root | HeadBucket) for the given source
// type, then exits.
//
// On success: prints `{"ok":true}` to stdout, exits 0.
// On failure: prints `step:reason` to stderr (e.g. `connect:dial tcp: timeout`),
// exits 1.
//
// The `step` is one of: connect | auth | mount | list | config — matching
// the API's TestResult schema. The API parses stderr to populate the step
// field for the UI.
func runTestConnection(args []string) {
	fs := flag.NewFlagSet("test-connection", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (ssh, smb, s3, nfs)")
	host := fs.String("host", "", "Host (ssh, smb)")
	port := fs.Int("port", 0, "Port (ssh, smb; default 22 / 445)")
	user := fs.String("user", "", "Username (ssh, smb) or access key ID (s3)")
	password := fs.String("password", "", "Password (insecure — prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read creds from stdin: {\"password\":\"…\",\"key_passphrase\":\"…\"}")
	keyPath := fs.String("key", "", "SSH key path")
	knownHosts := fs.String("known-hosts", "", "SSH known_hosts path (required for ssh)")
	share := fs.String("share", "", "SMB share")
	bucket := fs.String("bucket", "", "S3 bucket")
	region := fs.String("region", "us-east-1", "S3 region")
	endpoint := fs.String("endpoint", "", "S3 endpoint URL (non-AWS)")
	exportPath := fs.String("export-path", "", "NFS export path to validate")
	authUID := fs.Int("auth-uid", 0, "NFS AUTH_SYS uid (default 0; servers with root_squash may require a non-root uid)")
	authGID := fs.Int("auth-gid", 0, "NFS AUTH_SYS gid (default 0)")
	authAuxGIDs := fs.String("auth-aux-gids", "", "NFS AUTH_SYS auxiliary GIDs, comma-separated (max 16)")
	probeTimeout := fs.Int("timeout", 0, "Per-probe timeout in seconds (default 5; clamped to [1,60])")
	_ = fs.Parse(args)

	pw := *password
	keyPassphrase := ""
	if *passwordStdin {
		creds := readCredsFromStdin()
		pw = creds.Password
		keyPassphrase = creds.KeyPassphrase
	}

	var ok bool
	var step, msg string

	switch *srcType {
	case "ssh":
		p := *port
		if p == 0 {
			p = 22
		}
		ok, step, msg = testSSH(*host, p, *user, pw, *keyPath, keyPassphrase, *knownHosts)
	case "smb":
		p := *port
		if p == 0 {
			p = 445
		}
		ok, step, msg = testSMB(*host, p, *user, pw, *share)
	case "s3":
		ok, step, msg = testS3(*endpoint, *bucket, *region, *user, pw)
	case "nfs":
		p := *port
		if p == 0 {
			p = 2049
		}
		// NFS handles its own stdout/stderr because the success JSON
		// carries an extra `tier` field naming which protocol path
		// validated the export (mount3 / nfsv4 / tcp). Done in-line
		// rather than via the (ok, step, msg) shape used by the others.
		runTestNFS(*host, p, *exportPath, uint32(*authUID), uint32(*authGID), parseAuxGIDs(*authAuxGIDs), *probeTimeout)
		return
	default:
		fmt.Fprintln(os.Stderr, "config:unsupported type "+*srcType)
		os.Exit(1)
	}

	if ok {
		fmt.Fprintln(os.Stdout, `{"ok":true}`)
		return
	}
	fmt.Fprintf(os.Stderr, "%s:%s\n", step, msg)
	os.Exit(1)
}

// classifySSHError maps an SSHConnector.Connect() error to (step, reason).
// The connector wraps each failure with a known prefix; we match on those.
func classifySSHError(err error) (step, msg string) {
	s := err.Error()
	switch {
	case strings.HasPrefix(s, "ssh dial"):
		return "connect", strings.TrimPrefix(s, "ssh dial ")
	case strings.HasPrefix(s, "load known_hosts"):
		return "config", s
	case strings.HasPrefix(s, "read ssh key"), strings.HasPrefix(s, "parse ssh key"):
		return "config", s
	case strings.HasPrefix(s, "sftp client"):
		return "list", strings.TrimPrefix(s, "sftp client: ")
	default:
		// SSH auth failures land here (gossh.Dial returns "ssh: handshake
		// failed: …" or "unable to authenticate" wrapped under "ssh dial").
		// They're already covered by the dial branch above. Anything left is
		// likely auth-related.
		return "auth", s
	}
}

func testSSH(host string, port int, user, password, keyPath, keyPassphrase, knownHosts string) (ok bool, step, msg string) {
	if host == "" || user == "" {
		return false, "config", "host and user required"
	}
	if knownHosts == "" {
		return false, "config", "known_hosts required (strict by default)"
	}

	c := connector.NewSSHConnector(host, port, user, password, keyPath, keyPassphrase, knownHosts)
	if err := c.Connect(context.Background()); err != nil {
		s, m := classifySSHError(err)
		return false, s, m
	}
	defer c.Close()
	return true, "", ""
}

func classifySMBError(err error) (step, msg string) {
	s := err.Error()
	switch {
	case strings.HasPrefix(s, "smb dial"):
		return "connect", strings.TrimPrefix(s, "smb dial ")
	case strings.HasPrefix(s, "smb session"):
		return "auth", strings.TrimPrefix(s, "smb session: ")
	case strings.HasPrefix(s, "smb mount"):
		return "mount", strings.TrimPrefix(s, "smb mount ")
	default:
		return "connect", s
	}
}

func testSMB(host string, port int, user, password, share string) (ok bool, step, msg string) {
	if host == "" || user == "" || share == "" {
		return false, "config", "host, user, share required"
	}
	c := connector.NewSMBConnector(host, port, user, password, share)
	if err := c.Connect(context.Background()); err != nil {
		s, m := classifySMBError(err)
		return false, s, m
	}
	defer c.Close()
	return true, "", ""
}

func testS3(endpoint, bucket, region, accessKey, secretKey string) (ok bool, step, msg string) {
	if bucket == "" {
		return false, "config", "bucket required"
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")),
	)
	if err != nil {
		return false, "config", fmt.Sprintf("aws config: %v", err)
	}

	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
			o.UsePathStyle = true
		}
	})

	if _, err := client.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: aws.String(bucket)}); err != nil {
		s := err.Error()
		switch {
		case strings.Contains(s, "no such host"), strings.Contains(s, "connection refused"):
			return false, "connect", s
		case strings.Contains(s, "InvalidAccessKeyId"), strings.Contains(s, "SignatureDoesNotMatch"):
			return false, "auth", s
		// Only the explicit S3-API codes — bare "NotFound" is too broad
		// (HTTP 404 from a misconfigured endpoint URL would otherwise read
		// as "bucket not found", masking the real config issue).
		case strings.Contains(s, "NoSuchBucket"):
			return false, "list", fmt.Sprintf("bucket %q not found", bucket)
		case strings.Contains(s, "Forbidden"):
			return false, "auth", "access denied"
		default:
			return false, "list", s
		}
	}
	return true, "", ""
}

// runTestNFS dispatches to the nfsprobe cascade and writes its own
// success/failure to stdout/stderr, then exits. Done out-of-band from
// the (ok, step, msg) shape because the success JSON carries an
// additional `tier` field that the API surfaces to the UI.
//
// timeout is per-RPC; clamped to [1, 60] seconds to bound how long a
// hung server can block the source-creation form. The outer context
// gets ~3× the per-RPC timeout because the cascade may make multiple
// RPC round-trips.
func runTestNFS(
	host string, port int, exportPath string,
	authUID, authGID uint32, auxGIDs []uint32, timeoutSeconds int,
) {
	if timeoutSeconds <= 0 {
		timeoutSeconds = 5
	}
	if timeoutSeconds < 1 {
		timeoutSeconds = 1
	}
	if timeoutSeconds > 60 {
		timeoutSeconds = 60
	}
	perRPCTimeout := time.Duration(timeoutSeconds) * time.Second
	ctx, cancel := context.WithTimeout(context.Background(), 3*perRPCTimeout)
	defer cancel()
	res, err := nfsprobe.Probe(ctx, nfsprobe.ProbeOptions{
		Host:        host,
		Port:        uint32(port),
		ExportPath:  exportPath,
		AuthMethod:  nfsprobe.AuthSys,
		AuthUID:     authUID,
		AuthGID:     authGID,
		AuthAuxGIDs: auxGIDs,
		Timeout:     perRPCTimeout,
	})
	if err != nil {
		var pe *nfsprobe.ProbeError
		if errors.As(err, &pe) {
			fmt.Fprintf(os.Stderr, "%s:%s\n", string(pe.Step), pe.Msg)
		} else {
			fmt.Fprintf(os.Stderr, "connect:%s\n", err.Error())
		}
		os.Exit(1)
	}
	if res != nil && res.OK {
		out := fmt.Sprintf(`{"ok":true,"tier":%q}`, string(res.Tier))
		if res.Warning != "" {
			out = fmt.Sprintf(`{"ok":true,"tier":%q,"warn":%q}`,
				string(res.Tier), res.Warning)
		}
		fmt.Fprintln(os.Stdout, out)
		return
	}
	// Defensive: nfsprobe.Probe should always return either a typed
	// error or a non-nil success result. Reaching here means a bug.
	fmt.Fprintln(os.Stderr, "connect:nfsprobe returned no result")
	os.Exit(1)
}

// parseAuxGIDs converts a comma-separated GID list into the uint32
// slice the probe's AUTH_SYS builder expects. Whitespace and empty
// fragments are tolerated (e.g., "27, 100,," parses as [27, 100]).
// Non-numeric fragments are silently dropped — the form-side validator
// is the right place to surface those, not the scanner CLI.
//
// Surplus entries beyond 16 are NOT trimmed here; the probe's
// authSysBuilder enforces the RFC 5531 cap.
func parseAuxGIDs(raw string) []uint32 {
	if raw == "" {
		return nil
	}
	fields := strings.Split(raw, ",")
	out := make([]uint32, 0, len(fields))
	for _, f := range fields {
		s := strings.TrimSpace(f)
		if s == "" {
			continue
		}
		v, err := strconv.ParseUint(s, 10, 32)
		if err != nil {
			continue
		}
		out = append(out, uint32(v))
	}
	return out
}

// _legacyTCPNFS is the pre-Phase-3a TCP-only probe. Kept for one
// release as a reference / fallback we could swap back to; remove
// after Phase 3 soaks.
//
//goland:noinspection GoUnusedFunction
func _legacyTCPNFS(host string, port int, _exportPath string) (ok bool, step, msg string) {
	if host == "" {
		return false, "config", "host required"
	}
	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	conn, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		s := err.Error()
		if i := strings.LastIndex(s, ": "); i > 0 && strings.HasPrefix(s, "dial tcp") {
			s = s[i+2:]
		}
		return false, "connect", s
	}
	_ = conn.Close()
	return true, "", ""
}
