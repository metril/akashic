package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/lsarpc"
)

// runResolveName handles `resolve-name`: LsarLookupNames3 over the SMB
// IPC$ share, translating NAME (user, DOMAIN\user or user@domain) to a SID.
//
// Stdout: {"sid":"S-1-5-21-…-1104","domain":"DOMAIN","domain_sid":"S-1-5-21-…","kind":"user"}
//
// Exit codes: 0 success, 2 name not found (one-line stderr), 1 other failure.
// With --password-stdin the password is read from a {"password":"…"} line.
func runResolveName(args []string) {
	fs := flag.NewFlagSet("resolve-name", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (smb)")
	host := fs.String("host", "", "SMB host")
	port := fs.Int("port", 445, "SMB port")
	user := fs.String("user", "", "SMB username")
	password := fs.String("password", "", "SMB password (insecure — visible in ps; prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read password from stdin as a JSON line: {\"password\":\"…\"}")
	domain := fs.String("domain", "", "Expected domain: qualifies bare names and rejects results from another domain")
	name := fs.String("name", "", "Name to resolve: user, DOMAIN\\user or user@domain")
	_ = fs.Parse(args)

	if err := validateResolveNameArgs(*srcType, *host, *user, *name); err != nil {
		fail("resolve-name: %v", err)
	}
	pw := *password
	if *passwordStdin {
		pw = readPasswordFromStdin()
	}

	res, err := lookupNameViaLsa(*host, *port, *user, pw, qualifyName(strings.TrimSpace(*name), strings.TrimSpace(*domain)))
	if err != nil {
		if errors.Is(err, lsarpc.ErrNameNotFound) {
			fmt.Fprintf(os.Stderr, "resolve-name: name not found: %s\n", *name)
			os.Exit(2)
		}
		fail("resolve-name: %v", err)
	}
	if err := classifyResolvedName(res, strings.TrimSpace(*domain)); err != nil {
		fmt.Fprintf(os.Stderr, "resolve-name: %v: %s\n", err, *name)
		os.Exit(2)
	}
	if err := json.NewEncoder(os.Stdout).Encode(resolveNameOutput(res)); err != nil {
		fail("resolve-name: write output: %v", err)
	}
}

type resolveNameResult struct {
	Sid       string `json:"sid"`
	Domain    string `json:"domain"`
	DomainSid string `json:"domain_sid"`
	Kind      string `json:"kind"`
}

func resolveNameOutput(r lsarpc.NameResult) resolveNameResult {
	return resolveNameResult{Sid: r.SidString, Domain: r.Domain, DomainSid: r.DomainSid, Kind: lsarpc.SidTypeName(r.SidType)}
}

func validateResolveNameArgs(srcType, host, user, name string) error {
	if srcType != "smb" {
		return errors.New("only --type=smb is supported")
	}
	if host == "" || user == "" || strings.TrimSpace(name) == "" {
		return errors.New("--host, --user, and --name are required")
	}
	return nil
}

func lookupNameViaLsa(host string, port int, user, password, name string) (lsarpc.NameResult, error) {
	transport, err := connector.OpenLsaPipe(host, port, user, password)
	if err != nil {
		return lsarpc.NameResult{}, fmt.Errorf("open lsarpc pipe: %w", err)
	}
	cl := lsarpc.NewClient(transport)
	defer cl.Close()
	if err := cl.Bind(); err != nil {
		return lsarpc.NameResult{}, fmt.Errorf("lsarpc bind: %w", err)
	}
	if err := cl.Open(); err != nil {
		return lsarpc.NameResult{}, fmt.Errorf("lsarpc open policy: %w", err)
	}
	res, err := cl.LookupNames([]string{name})
	if err != nil {
		return lsarpc.NameResult{}, err
	}
	return res[0], nil
}

// qualifyName prefixes a bare name with "DOMAIN\\" when a domain is given.
func qualifyName(name, domain string) string {
	if domain == "" || strings.ContainsAny(name, `\@`) {
		return name
	}
	return domain + `\` + name
}

// classifyResolvedName accepts only a domain user SID (S-1-5-21-…) whose
// type is user and, when domain is set, whose domain matches. Groups,
// aliases, BUILTIN and well-known SIDs must never resolve as a person.
func classifyResolvedName(r lsarpc.NameResult, domain string) error {
	if !r.Mapped {
		return errors.New("name not found")
	}
	if r.SidType != lsarpc.SidTypeUser {
		k := lsarpc.SidTypeName(r.SidType)
		if k == "" {
			k = "unknown"
		}
		return fmt.Errorf("name is not a user (kind=%s)", k)
	}
	if !strings.HasPrefix(r.SidString, "S-1-5-21-") {
		return errors.New("name is not a domain user (builtin or well-known SID)")
	}
	if domain != "" && !strings.EqualFold(r.Domain, domain) {
		return fmt.Errorf("name resolved in domain %q, not %q", r.Domain, domain)
	}
	return nil
}
