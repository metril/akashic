package main

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/lsarpc"
	"github.com/akashic-project/akashic/scanner/internal/metadata"
)

// runResolveSids handles the `resolve-sids` subcommand. It opens an
// LSARPC pipe over the SMB IPC$ share, batch-resolves all input SIDs
// in a single LsarLookupSids2 call, merges in well-known table hits,
// and emits one JSON object to stdout:
//
//	{
//	  "resolved": {
//	    "S-1-5-32-544": {"name": "BUILTIN\\Administrators", "domain": "BUILTIN", "kind": "alias"},
//	    ...
//	  },
//	  "unresolved": ["S-1-5-21-...-99999"]
//	}
//
// Exit codes:
//
//	0 — success (well-known + LSA combined; unresolved is allowed to be non-empty)
//	1 — usage / connection / RPC failure (one-line error on stderr)
//
// All inputs that come from the api are passed via stdin as a single
// JSON line: {"password": "…", "sids": ["S-1-…", …]}. Both fields are
// always present; argv-side --password is supported only as a dev
// convenience.
func runResolveSids(args []string) {
	fs := flag.NewFlagSet("resolve-sids", flag.ExitOnError)
	srcType := fs.String("type", "", "Source type (smb)")
	host := fs.String("host", "", "SMB host")
	port := fs.Int("port", 445, "SMB port")
	user := fs.String("user", "", "SMB username")
	password := fs.String("password", "", "SMB password (insecure — visible in ps; prefer --password-stdin)")
	passwordStdin := fs.Bool("password-stdin", false, "Read password+sids from stdin as a JSON line: {\"password\":\"…\",\"sids\":[…]}")
	sidsCSV := fs.String("sids", "", "Comma-separated SIDs (only used when --password-stdin is NOT set)")
	_ = fs.Parse(args)

	if *srcType != "smb" {
		fail("resolve-sids: only --type=smb is supported")
	}
	if *host == "" || *user == "" {
		fail("resolve-sids: --host and --user are required")
	}

	pw := *password
	var sids []string
	if *passwordStdin {
		input := readResolveSidsStdin()
		pw = input.Password
		sids = input.Sids
	} else if *sidsCSV != "" {
		for _, s := range strings.Split(*sidsCSV, ",") {
			if s = strings.TrimSpace(s); s != "" {
				sids = append(sids, s)
			}
		}
	}
	if len(sids) == 0 {
		fail("resolve-sids: at least one SID required (--sids or stdin)")
	}

	// Two-phase resolution: well-known table first (no network),
	// remainder via LSARPC. Lets us avoid an SMB connection entirely
	// for an all-built-in SID list (common for read-mostly ACLs that
	// reference SYSTEM, Administrators, Authenticated Users, …).
	resolved := make(map[string]resolvedSid, len(sids))
	var remaining []string
	for _, s := range sids {
		if name := metadata.WellKnownSIDName(s); name != "" {
			dom, rel := splitDomainName(name)
			resolved[s] = resolvedSid{Name: rel, Domain: dom, Kind: "well_known_group"}
		} else {
			remaining = append(remaining, s)
		}
	}

	if len(remaining) > 0 {
		extra, err := resolveViaLsa(*host, *port, *user, pw, remaining)
		if err != nil {
			fail("resolve-sids: %v", err)
		}
		for sid, r := range extra {
			resolved[sid] = r
		}
	}

	// Compute unresolved list — anything in the input that isn't in
	// `resolved` (or is in `resolved` but with empty Name).
	var unresolved []string
	for _, s := range sids {
		if r, ok := resolved[s]; !ok || r.Name == "" {
			unresolved = append(unresolved, s)
			delete(resolved, s) // empty rows shouldn't appear in `resolved`
		}
	}
	if unresolved == nil {
		unresolved = []string{}
	}

	out := struct {
		Resolved   map[string]resolvedSid `json:"resolved"`
		Unresolved []string               `json:"unresolved"`
	}{Resolved: resolved, Unresolved: unresolved}
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil {
		fail("resolve-sids: write output: %v", err)
	}
}

// resolvedSid is the shape stored in the api side's principals_cache
// table — kept narrow so wire payload changes are explicit.
type resolvedSid struct {
	Name   string `json:"name"`
	Domain string `json:"domain,omitempty"`
	Kind   string `json:"kind,omitempty"`
}

// resolveViaLsa opens an LSARPC pipe, runs one LsarLookupSids2 call
// against all input SIDs, and returns a map of resolved entries. SIDs
// that LSA returned empty for are simply absent from the map (caller
// puts them in the `unresolved` list).
func resolveViaLsa(host string, port int, user, password string, sids []string) (map[string]resolvedSid, error) {
	transport, err := connector.OpenLsaPipe(host, port, user, password)
	if err != nil {
		return nil, fmt.Errorf("open lsarpc pipe: %w", err)
	}
	cl := lsarpc.NewClient(transport)
	defer cl.Close()

	if err := cl.Bind(); err != nil {
		return nil, fmt.Errorf("lsarpc bind: %w", err)
	}
	if err := cl.Open(); err != nil {
		return nil, fmt.Errorf("lsarpc open policy: %w", err)
	}

	bin := make([][]byte, 0, len(sids))
	keep := make([]string, 0, len(sids))
	for _, s := range sids {
		if b := sidStringToLsaBytes(s); b != nil {
			bin = append(bin, b)
			keep = append(keep, s)
		}
	}
	if len(bin) == 0 {
		return map[string]resolvedSid{}, nil
	}

	results, err := cl.LookupWithDomains(bin)
	if err != nil {
		return nil, fmt.Errorf("lsarpc lookup: %w", err)
	}

	out := make(map[string]resolvedSid, len(results))
	for i, r := range results {
		if i >= len(keep) {
			break
		}
		if r.Name == "" {
			continue
		}
		display := r.Name
		if r.Domain != "" {
			display = r.Domain + `\` + r.Name
		}
		out[keep[i]] = resolvedSid{
			Name:   display,
			Domain: r.Domain,
			Kind:   lsarpc.SidTypeName(r.SidType),
		}
	}
	return out, nil
}

// sidStringToLsaBytes converts an "S-1-5-21-…" string to the on-wire
// SID byte sequence LSARPC wants: revision, sub-authority count,
// 6-byte big-endian authority, then sub-authorities little-endian.
// Returns nil for malformed input — the LSA call simply skips bad
// inputs in that case.
func sidStringToLsaBytes(s string) []byte {
	parts := strings.Split(s, "-")
	if len(parts) < 3 || (parts[0] != "S" && parts[0] != "s") {
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
	// 6-byte big-endian authority.
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

// splitDomainName splits a "DOMAIN\name" string into its parts. If the
// input has no backslash (e.g., bare "Everyone"), the domain is empty
// and the entire input goes in the relative-name slot.
func splitDomainName(s string) (domain, rel string) {
	if i := strings.IndexByte(s, '\\'); i >= 0 {
		return s[:i], s[i+1:]
	}
	return "", s
}

// resolveSidsStdin is the JSON shape the api pipes in via --password-stdin.
// Both fields are required (the api always supplies both, even when
// password is empty for guest SMB).
type resolveSidsStdin struct {
	Password string   `json:"password"`
	Sids     []string `json:"sids"`
}

func readResolveSidsStdin() resolveSidsStdin {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 4096), 1<<20)
	if !scanner.Scan() {
		return resolveSidsStdin{}
	}
	var payload resolveSidsStdin
	if err := json.Unmarshal(scanner.Bytes(), &payload); err != nil {
		return resolveSidsStdin{}
	}
	return payload
}

// fail writes a single-line error to stderr and exits 1. Mirrors the
// existing pattern in resolve_groups.go.
func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}
