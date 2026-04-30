package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"

	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/samr"
)

// runResolveGroups handles the `resolve-groups` subcommand. It takes the
// SMB connection details and a target SID, runs the SAMR sequence, and
// prints {"groups": [...], "source": "samr"} to stdout.
//
// Exit codes:
//
//	0 — success (JSON on stdout)
//	1 — generic failure (one-line error on stderr)
//	2 — user not found in domain (one-line error on stderr)
//
// The password may be passed either via --password (plaintext, visible in
// /proc/<pid>/cmdline) or via --password-stdin. The latter reads a single
// JSON line from stdin: {"password":"…"}. The API caller uses the stdin
// path so credentials don't show up in process listings.
func runResolveGroups(args []string) {
	fs := flag.NewFlagSet("resolve-groups", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (smb)")
	host := fs.String("host", "", "SMB host")
	port := fs.Int("port", 445, "SMB port")
	user := fs.String("user", "", "SMB username")
	password := fs.String("password", "", "SMB password (insecure — visible in ps; prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read password from stdin as a JSON line: {\"password\":\"…\"}")
	sidStr := fs.String("sid", "", "User SID to resolve groups for (S-1-…)")
	_ = fs.Parse(args)

	if *srcType != "smb" {
		fmt.Fprintln(os.Stderr, "resolve-groups: only --type=smb is supported")
		os.Exit(1)
	}
	if *host == "" || *user == "" || *sidStr == "" {
		fmt.Fprintln(os.Stderr, "resolve-groups: --host, --user, and --sid are required")
		os.Exit(1)
	}

	pw := *password
	if *passwordStdin {
		pw = readPasswordFromStdin()
	}

	sid, err := samr.ParseSidString(*sidStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "resolve-groups: bad sid: %v\n", err)
		os.Exit(1)
	}

	transport, err := connector.OpenSamrPipe(*host, *port, *user, pw)
	if err != nil {
		fmt.Fprintf(os.Stderr, "resolve-groups: pipe: %v\n", err)
		os.Exit(1)
	}
	// Note: ResolveGroupsForSid takes ownership and Closes transport itself.

	server := fmt.Sprintf("\\\\%s", *host)
	groups, err := samr.ResolveGroupsForSid(transport, server, sid)
	if err != nil {
		// Differentiate "user not found in domain" from other failures by
		// inspecting the wrapped NTSTATUS — only STATUS_NO_SUCH_USER /
		// STATUS_NONE_MAPPED are genuinely "not found"; ACCESS_DENIED and
		// friends are real backend errors that the API should surface as
		// such, not silently report as "no such user" (which would mask
		// permission misconfigurations).
		var statusErr *samr.StatusError
		if errors.As(err, &statusErr) && statusErr.IsNotFound() {
			fmt.Fprintf(os.Stderr, "resolve-groups: user not found in domain (ntstatus=0x%x)\n", statusErr.Status)
			os.Exit(2)
		}
		fmt.Fprintf(os.Stderr, "resolve-groups: %v\n", err)
		os.Exit(1)
	}

	if groups == nil {
		groups = []string{}
	}
	out := struct {
		Groups []string `json:"groups"`
		Source string   `json:"source"`
	}{Groups: groups, Source: "samr"}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fmt.Fprintf(os.Stderr, "resolve-groups: write output: %v\n", err)
		os.Exit(1)
	}
}

// stdinCreds bundles the credentials the api may pipe to the scanner over
// stdin so they don't appear in /proc/<pid>/cmdline. Empty struct returned
// on parse failure (caller surfaces auth errors from the remote server).
type stdinCreds struct {
	Password      string `json:"password"`
	KeyPassphrase string `json:"key_passphrase"`
	// Krb5Password is sent for NFS sources whose auth_method is krb5*
	// and which were configured with a password rather than a keytab.
	// Kept distinct from Password so the SSH/SMB password and the
	// kerberos password don't collide on a future shared-creds source
	// type.
	Krb5Password string `json:"krb5_password"`
}

func readCredsFromStdin() stdinCreds {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 4096), 64*1024)
	if !scanner.Scan() {
		return stdinCreds{}
	}
	var payload stdinCreds
	if err := json.Unmarshal(scanner.Bytes(), &payload); err != nil {
		return stdinCreds{}
	}
	return payload
}

// readPasswordFromStdin is the legacy single-field accessor used by the
// `resolve-groups` subcommand. Equivalent to `readCredsFromStdin().Password`.
func readPasswordFromStdin() string {
	return readCredsFromStdin().Password
}
