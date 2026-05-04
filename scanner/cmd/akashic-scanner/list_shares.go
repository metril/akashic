package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/hirochachacha/go-smb2"

	"github.com/akashic-project/akashic/scanner/internal/nfsprobe"
)

// runListShares enumerates the share/export/bucket names visible
// to the supplied credentials. Mirrors test-connection's CLI shape:
// per-type flags, optional --password-stdin JSON creds, JSON stdout
// `{"shares": [...]}`, `step:reason` stderr on failure.
//
// SSH and local are intentionally not supported — there's no
// "shares" concept on either, and emulating one (e.g. listing the
// remote SSH "/" or the local "/") would mislead the user. The api
// rejects those at the endpoint level so this subcommand only sees
// types it can handle.
func runListShares(args []string) {
	fs := flag.NewFlagSet("list-shares", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (smb, nfs, s3)")
	host := fs.String("host", "", "Host (smb, nfs)")
	port := fs.Int("port", 0, "Port (smb default 445; nfs default 0 = portmap)")
	user := fs.String("user", "", "Username (smb) or access key ID (s3)")
	password := fs.String("password", "", "Password (insecure — prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read creds from stdin: {\"password\":\"…\"}")
	region := fs.String("region", "us-east-1", "S3 region")
	endpoint := fs.String("endpoint", "", "S3 endpoint URL (non-AWS)")
	timeoutS := fs.Int("timeout", 10, "Per-call timeout in seconds")
	_ = fs.Parse(args)

	pw := *password
	if *passwordStdin {
		pw = readCredsFromStdin().Password
	}

	var (
		shares []string
		step   string
		msg    string
	)

	switch *srcType {
	case "smb":
		p := *port
		if p == 0 {
			p = 445
		}
		shares, step, msg = listSMBShares(*host, p, *user, pw, time.Duration(*timeoutS)*time.Second)
	case "nfs":
		shares, step, msg = listNFSExports(*host, uint32(*port), time.Duration(*timeoutS)*time.Second)
	case "s3":
		shares, step, msg = listS3Buckets(*endpoint, *region, *user, pw, time.Duration(*timeoutS)*time.Second)
	default:
		fmt.Fprintf(os.Stderr, "config:list-shares does not support type %q\n", *srcType)
		os.Exit(1)
	}

	if step != "" {
		fmt.Fprintf(os.Stderr, "%s:%s\n", step, msg)
		os.Exit(1)
	}
	if shares == nil {
		shares = []string{}
	}
	out := struct {
		Shares []string `json:"shares"`
	}{Shares: shares}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fmt.Fprintf(os.Stderr, "list-shares: write: %v\n", err)
		os.Exit(1)
	}
}

// listSMBShares dials the host, authenticates, and calls
// NetShareEnumAll over the IPC$ srvsvc named pipe via go-smb2's
// Session.ListSharenames(). Bypasses SMBConnector because we don't
// have (or want to require) a share name to mount up-front.
func listSMBShares(host string, port int, user, password string, timeout time.Duration) ([]string, string, string) {
	if host == "" || user == "" {
		return nil, "config", "host and user required"
	}
	addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	d := net.Dialer{Timeout: timeout}
	conn, err := d.DialContext(ctx, "tcp", addr)
	if err != nil {
		return nil, "connect", err.Error()
	}
	defer conn.Close()

	dialer := &smb2.Dialer{
		Initiator: &smb2.NTLMInitiator{User: user, Password: password},
	}
	session, err := dialer.Dial(conn)
	if err != nil {
		return nil, "auth", err.Error()
	}
	defer session.Logoff()

	names, err := session.ListSharenames()
	if err != nil {
		return nil, "list", err.Error()
	}

	// Filter out the well-known administrative shares — IPC$, ADMIN$,
	// printer queues, hidden $-suffixed shares — they're never useful
	// for indexing and would clutter the picker. Users who actually
	// want one of these can still create the source by hand.
	out := make([]string, 0, len(names))
	for _, n := range names {
		if isAdministrativeShare(n) {
			continue
		}
		out = append(out, n)
	}
	return out, "", ""
}

func isAdministrativeShare(name string) bool {
	switch strings.ToUpper(name) {
	case "IPC$", "ADMIN$", "PRINT$":
		return true
	}
	// C$, D$, … and any other hidden $-suffixed share.
	return strings.HasSuffix(name, "$")
}

// listNFSExports issues MOUNT3 EXPORT and returns the export paths.
// Group lists are dropped — the picker only needs the path; the
// scanner enforces auth at scan time.
func listNFSExports(host string, mountdPort uint32, timeout time.Duration) ([]string, string, string) {
	if host == "" {
		return nil, "config", "host required"
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout*2)
	defer cancel()
	entries, err := nfsprobe.Exports(ctx, host, mountdPort, timeout)
	if err != nil {
		return nil, "list", err.Error()
	}
	out := make([]string, 0, len(entries))
	for _, e := range entries {
		out = append(out, e.Path)
	}
	return out, "", ""
}

// listS3Buckets calls ListBuckets — returns every bucket the
// credentials can see, regardless of region (the API is account-
// global and uses an aliased "us-east-1" endpoint internally).
func listS3Buckets(endpoint, region, accessKey, secretKey string, timeout time.Duration) ([]string, string, string) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cfg, err := awsconfig.LoadDefaultConfig(ctx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider(accessKey, secretKey, "")),
	)
	if err != nil {
		return nil, "config", fmt.Sprintf("aws config: %v", err)
	}
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
			o.UsePathStyle = true
		}
	})
	resp, err := client.ListBuckets(ctx, &s3.ListBucketsInput{})
	if err != nil {
		s := err.Error()
		switch {
		case strings.Contains(s, "no such host"), strings.Contains(s, "connection refused"):
			return nil, "connect", s
		case strings.Contains(s, "InvalidAccessKeyId"), strings.Contains(s, "SignatureDoesNotMatch"):
			return nil, "auth", s
		case strings.Contains(s, "Forbidden"):
			return nil, "auth", "access denied"
		default:
			return nil, "list", s
		}
	}
	out := make([]string, 0, len(resp.Buckets))
	for _, b := range resp.Buckets {
		out = append(out, aws.ToString(b.Name))
	}
	return out, "", ""
}
