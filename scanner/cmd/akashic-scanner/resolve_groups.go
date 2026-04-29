package main

import (
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
func runResolveGroups(args []string) {
	fs := flag.NewFlagSet("resolve-groups", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (smb)")
	host := fs.String("host", "", "SMB host")
	port := fs.Int("port", 445, "SMB port")
	user := fs.String("user", "", "SMB username")
	password := fs.String("password", "", "SMB password")
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

	sid, err := samr.ParseSidString(*sidStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "resolve-groups: bad sid: %v\n", err)
		os.Exit(1)
	}

	transport, err := connector.OpenSamrPipe(*host, *port, *user, *password)
	if err != nil {
		fmt.Fprintf(os.Stderr, "resolve-groups: pipe: %v\n", err)
		os.Exit(1)
	}
	// Note: ResolveGroupsForSid takes ownership and Closes transport itself.

	server := fmt.Sprintf("\\\\%s", *host)
	groups, err := samr.ResolveGroupsForSid(transport, server, sid)
	if err != nil {
		// Map the structural OpenUser failure to exit code 2 so the API
		// caller can disambiguate "user not found in domain" from
		// "couldn't even reach the server".
		if errors.Is(err, samr.ErrSamrOpenUserFailed) {
			fmt.Fprintf(os.Stderr, "resolve-groups: user not found in domain\n")
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
