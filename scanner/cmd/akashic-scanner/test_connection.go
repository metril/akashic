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
	srcType := fs.String("type", "", "Source type (smb, s3, nfs)")
	host := fs.String("host", "", "Host (smb)")
	port := fs.Int("port", 0, "Port (smb; default 445)")
	user := fs.String("user", "", "Username (smb) or access key ID (s3)")
	password := fs.String("password", "", "Password (insecure — prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read creds from stdin: {\"password\":\"…\"}")
	share := fs.String("share", "", "SMB share")
	bucket := fs.String("bucket", "", "S3 bucket")
	region := fs.String("region", "us-east-1", "S3 region")
	endpoint := fs.String("endpoint", "", "S3 endpoint URL (non-AWS)")
	// v0.8.1 — empty / "auto" lets testS3 derive (path-style when
	// endpoint is set, else virtual-hosted). "true" / "false" override
	// — Wasabi/B2 want "false" even with their endpoint set.
	pathStyle := fs.String("path-style", "", "S3 addressing style: auto | true | false (default auto)")
	exportPath := fs.String("export-path", "", "NFS export path to validate")
	authUID := fs.Int("auth-uid", 0, "NFS AUTH_SYS uid (default 0; servers with root_squash may require a non-root uid)")
	authGID := fs.Int("auth-gid", 0, "NFS AUTH_SYS gid (default 0)")
	authAuxGIDs := fs.String("auth-aux-gids", "", "NFS AUTH_SYS auxiliary GIDs, comma-separated (max 16)")
	probeTimeout := fs.Int("timeout", 0, "Per-probe timeout in seconds (default 5; clamped to [1,60])")
	authMethod := fs.String("auth-method", "sys", "NFS auth method: sys | krb5 | krb5i | krb5p (krb5i/krb5p not yet implemented)")
	krb5Principal := fs.String("krb5-principal", "", "NFS krb5 user-side principal (no @realm)")
	krb5Realm := fs.String("krb5-realm", "", "NFS krb5 realm (uppercase by convention)")
	krb5SPN := fs.String("krb5-service-principal", "", "NFS krb5 service principal name (default: nfs/<host>)")
	krb5Keytab := fs.String("krb5-keytab", "", "NFS krb5 keytab path (mutually exclusive with stdin password)")
	krb5Config := fs.String("krb5-config", "", "Alternate krb5.conf path (default: /etc/krb5.conf, then DNS-discovery fallback)")
	allowEmptyPw := fs.Bool("allow-empty-password", false, "SMB: opt into empty-password sessions (lab / anonymous-share configs)")
	_ = fs.Parse(args)

	pw := *password
	krb5Password := ""
	if *passwordStdin {
		creds := readCredsFromStdin()
		pw = creds.Password
		krb5Password = creds.Krb5Password
	}

	var ok bool
	var step, msg string

	switch *srcType {
	case "smb":
		p := *port
		if p == 0 {
			p = 445
		}
		ok, step, msg = testSMB(*host, p, *user, pw, *share, *allowEmptyPw)
	case "s3":
		ok, step, msg = testS3(*endpoint, *bucket, *region, *user, pw, *pathStyle)
	case "nfs":
		p := *port
		if p == 0 {
			p = 2049
		}
		// NFS handles its own stdout/stderr because the success JSON
		// carries an extra `tier` field naming which protocol path
		// validated the export (mount3 / nfsv4 / tcp). Done in-line
		// rather than via the (ok, step, msg) shape used by the others.
		runTestNFS(nfsTestArgs{
			Host:                 *host,
			Port:                 p,
			ExportPath:           *exportPath,
			AuthUID:              uint32(*authUID),
			AuthGID:              uint32(*authGID),
			AuxGIDs:              parseAuxGIDs(*authAuxGIDs),
			TimeoutSeconds:       *probeTimeout,
			AuthMethod:           *authMethod,
			Krb5Principal:        *krb5Principal,
			Krb5Realm:            *krb5Realm,
			Krb5ServicePrincipal: *krb5SPN,
			Krb5KeytabPath:       *krb5Keytab,
			Krb5Password:         krb5Password,
			Krb5ConfigPath:       *krb5Config,
		})
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

// classifySMBError maps an SMBConnector.Connect() error to (step, reason).
// The connector wraps each failure with a known prefix; we match on those.
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

func testSMB(host string, port int, user, password, share string, allowEmptyPw bool) (ok bool, step, msg string) {
	if host == "" || user == "" || share == "" {
		return false, "config", "host, user, share required"
	}
	// v0.29.5 — pre-empt go-smb2's empty-password permissiveness
	// (the vendor only rejects empty User). Match probe.runSMB so
	// the CLI surfaces the same diagnostic.
	if password == "" && !allowEmptyPw {
		return false, "config",
			"password required (empty-password SMB scans are not supported; " +
				"some servers accept this as an authenticated session against a " +
				"null-password account, masking real auth failures — set " +
				"connection_config.allow_empty_password=true to explicitly opt in)"
	}
	c := connector.NewSMBConnector(host, port, user, password, share)
	if allowEmptyPw {
		c.SetAllowEmptyPassword(true)
	}
	if err := c.Connect(context.Background()); err != nil {
		s, m := classifySMBError(err)
		return false, s, m
	}
	defer c.Close()
	return true, "", ""
}

func testS3(endpoint, bucket, region, accessKey, secretKey, pathStyleFlag string) (ok bool, step, msg string) {
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

	// v0.8.1 — auto (empty/auto) → path-style when endpoint is set,
	// virtual-hosted otherwise. Explicit true/false honoured for the
	// providers where the auto rule is wrong (Wasabi/B2 with endpoint
	// set want virtual-hosted; reverse-proxied AWS-shaped URLs may
	// want path-style).
	usePath := endpoint != ""
	switch strings.ToLower(strings.TrimSpace(pathStyleFlag)) {
	case "true", "1", "yes":
		usePath = true
	case "false", "0", "no":
		usePath = false
	}

	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
		o.UsePathStyle = usePath
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

// nfsTestArgs bundles every flag testNFS needs into one struct. The
// extra one-line indirection over passing a half-dozen positional
// args keeps the call site readable now that AUTH_SYS and krb5* are
// both supported.
type nfsTestArgs struct {
	Host           string
	Port           int
	ExportPath     string
	AuthUID        uint32
	AuthGID        uint32
	AuxGIDs        []uint32
	TimeoutSeconds int

	AuthMethod           string
	Krb5Principal        string
	Krb5Realm            string
	Krb5ServicePrincipal string
	Krb5KeytabPath       string
	Krb5Password         string
	Krb5ConfigPath       string
}

func runTestNFS(a nfsTestArgs) {
	timeoutSeconds := a.TimeoutSeconds
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

	method := nfsprobe.AuthMethod(a.AuthMethod)
	if method == "" {
		method = nfsprobe.AuthSys
	}

	// Krb5 setup typically requires more time: TGS_REQ to the KDC,
	// followed by INIT exchange, followed by LOOKUP — three serial round
	// trips minimum. We give it 5× the per-RPC timeout instead of 3×
	// for non-krb5; still bounded by the API-side subprocess kill.
	outerMultiplier := 3
	if method == nfsprobe.AuthKrb5 || method == nfsprobe.AuthKrb5Integrity || method == nfsprobe.AuthKrb5Privacy {
		outerMultiplier = 5
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(outerMultiplier)*perRPCTimeout)
	defer cancel()

	res, err := nfsprobe.Probe(ctx, nfsprobe.ProbeOptions{
		Host:                 a.Host,
		Port:                 uint32(a.Port),
		ExportPath:           a.ExportPath,
		AuthMethod:           method,
		AuthUID:              a.AuthUID,
		AuthGID:              a.AuthGID,
		AuthAuxGIDs:          a.AuxGIDs,
		Timeout:              perRPCTimeout,
		Krb5Principal:        a.Krb5Principal,
		Krb5Realm:            a.Krb5Realm,
		Krb5ServicePrincipal: a.Krb5ServicePrincipal,
		Krb5KeytabPath:       a.Krb5KeytabPath,
		Krb5Password:         a.Krb5Password,
		Krb5ConfigPath:       a.Krb5ConfigPath,
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
