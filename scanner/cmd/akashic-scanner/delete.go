package main

import (
	"context"
	"flag"
	"fmt"
	"os"
)

// runDelete handles the `delete` subcommand. It opens a connection to the
// given source and removes the file at --path. On success: prints
// `{"ok":true}` to stdout, exits 0. On failure: prints `step:reason` to
// stderr and exits 1.
//
// `step` follows the same convention as `test-connection` and `fetch`:
// connect | auth | config | delete (the new step for the actual remove
// operation). The api side parses these to surface a useful error per
// failed copy in the bulk-delete response.
//
// Credentials come from stdin JSON ({"password":"…","key_passphrase":"…"})
// when --password-stdin is set — same shape `fetch` uses, so callers
// can reuse their secret-marshalling helper.
func runDelete(args []string) {
	fs := flag.NewFlagSet("delete", flag.ExitOnError)
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

	if err := conn.Delete(ctx, *path); err != nil {
		// "delete" step covers everything from the actual file removal
		// (permission denied, file not found, write-protected mount,
		// versioned bucket rejection, etc.). The classifier on the api
		// side preserves these verbatim.
		fmt.Fprintf(os.Stderr, "delete:%v\n", err)
		os.Exit(1)
	}

	fmt.Fprintln(os.Stdout, `{"ok":true}`)
}
