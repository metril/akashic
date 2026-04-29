package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/akashic-project/akashic/scanner/internal/connector"
)

// runFetch handles the `fetch` subcommand. It opens a connection to the
// given source, reads the file at --path, and pipes the bytes to stdout.
//
// Output: stdout is the raw file bytes (no JSON wrapping). Errors go to
// stderr as `step:reason` (open|connect|auth|list|config) and exit 1.
//
// Credentials come from stdin JSON ({"password":"…","key_passphrase":"…"})
// when --password-stdin is set.
func runFetch(args []string) {
	fs := flag.NewFlagSet("fetch", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (local, ssh, smb, nfs, s3)")
	host := fs.String("host", "", "Host (ssh, smb)")
	port := fs.Int("port", 0, "Port")
	user := fs.String("user", "", "Username (ssh, smb) or access key ID (s3)")
	password := fs.String("password", "", "Password (insecure — prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read creds from stdin")
	keyPath := fs.String("key", "", "SSH key path")
	knownHosts := fs.String("known-hosts", "", "SSH known_hosts path")
	share := fs.String("share", "", "SMB share")
	bucket := fs.String("bucket", "", "S3 bucket")
	region := fs.String("region", "us-east-1", "S3 region")
	endpoint := fs.String("endpoint", "", "S3 endpoint URL")
	path := fs.String("path", "", "Absolute path of the file inside the source")
	_ = fs.Parse(args)

	if *path == "" {
		fmt.Fprintln(os.Stderr, "config:--path is required")
		os.Exit(1)
	}

	pw := *password
	keyPassphrase := ""
	if *passwordStdin {
		creds := readCredsFromStdin()
		pw = creds.Password
		keyPassphrase = creds.KeyPassphrase
	}

	conn, step, err := buildConnector(*srcType, *host, *port, *user, pw, *keyPath, keyPassphrase, *knownHosts, *share, *bucket, *region, *endpoint)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s:%v\n", step, err)
		os.Exit(1)
	}
	defer conn.Close()

	ctx := context.Background()
	if err := conn.Connect(ctx); err != nil {
		fmt.Fprintf(os.Stderr, "connect:%v\n", err)
		os.Exit(1)
	}

	rc, err := conn.ReadFile(ctx, *path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open:%v\n", err)
		os.Exit(1)
	}
	defer rc.Close()

	if _, err := io.Copy(os.Stdout, rc); err != nil {
		// Stdout may have already received some bytes — the api side will
		// observe the truncation as an early-EOF + the non-zero exit.
		fmt.Fprintf(os.Stderr, "open:copy: %v\n", err)
		os.Exit(1)
	}
}

// buildConnector returns a connector for the given source type, plus the
// classification step name to use if the constructor itself rejects the
// args (e.g., unsupported type). It does not call Connect — that's the
// caller's job (so the caller can map connect errors to the "connect" step).
func buildConnector(
	srcType, host string, port int,
	user, password, keyPath, keyPassphrase, knownHosts string,
	share, bucket, region, endpoint string,
) (connector.Connector, string, error) {
	switch srcType {
	case "local":
		return connector.NewLocalConnector(), "", nil
	case "nfs":
		return connector.NewNFSConnector(), "", nil
	case "ssh":
		p := port
		if p == 0 {
			p = 22
		}
		return connector.NewSSHConnector(host, p, user, password, keyPath, keyPassphrase, knownHosts), "", nil
	case "smb":
		p := port
		if p == 0 {
			p = 445
		}
		return connector.NewSMBConnector(host, p, user, password, share), "", nil
	case "s3":
		return connector.NewS3Connector(endpoint, bucket, region, user, password), "", nil
	default:
		return nil, "config", fmt.Errorf("unsupported source type %q", srcType)
	}
}
