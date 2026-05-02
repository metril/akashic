package main

import (
	"fmt"
	"log"
	"os"
	"strings"
)

// runAuto is the "smart entrypoint" mode: pick the right subcommand
// based on what's already on disk + what's in the environment, then
// exec into it. Designed for the bundled docker-compose `scanner`
// service so a single command works on every install state:
//
//	first run with discovery enabled    → discover (operator approves)
//	first run with a claim token        → claim
//	subsequent runs (credentials exist) → agent
//
// Env vars (defaults match the standalone subcommands):
//
//	AKASHIC_API_URL       (required)  api base URL
//	AKASHIC_KEY_PATH      default /secrets/scanner.key
//	AKASHIC_ID_FILE       default /secrets/scanner.id
//	AKASHIC_CLAIM_TOKEN   if set, runs `claim --token=…`
//	AKASHIC_DISCOVER      truthy ("1","true","yes") → runs `discover`
//	SCANNER_ID            legacy fallback for the scanner UUID
//	                      (preserves existing installs that have
//	                      this in .env from the pre-v0.3.0 bootstrap)
//
// Exit codes:
//
//	0   handed off to a subcommand successfully
//	78  EX_CONFIG — couldn't determine which mode to run in
//	    (no creds, no token, discovery not enabled)
func runAuto(args []string) {
	_ = args // auto takes no flags; everything is env-driven

	apiURL := strings.TrimRight(os.Getenv("AKASHIC_API_URL"), "/")
	if apiURL == "" {
		log.Fatal("auto: AKASHIC_API_URL is required (e.g. http://api:8000)")
	}
	keyPath := envOr("AKASHIC_KEY_PATH", "/secrets/scanner.key")
	idFile := envOr("AKASHIC_ID_FILE", "/secrets/scanner.id")

	// Branch 1: full credentials on disk → straight to agent.
	scannerID := readScannerID(idFile)
	if scannerID == "" {
		// Pre-v0.3.0 installs put the id in .env, not in /secrets.
		scannerID = os.Getenv("SCANNER_ID")
	}
	keyExists := fileExists(keyPath)
	if keyExists && scannerID != "" {
		fmt.Fprintf(os.Stderr,
			"auto: existing credentials found (id=%s); running agent\n",
			scannerID)
		execAgent(apiURL, scannerID, keyPath)
		return // execAgent does not return on success
	}

	// Branch 2: claim token in env → run claim, which writes the
	// keypair + id and chains into agent via --start-after.
	token := os.Getenv("AKASHIC_CLAIM_TOKEN")
	if token != "" {
		if keyExists {
			log.Fatalf(
				"auto: $AKASHIC_CLAIM_TOKEN is set but %s already exists. "+
					"Either claim is unnecessary (delete the env var to use the "+
					"existing key) or the existing key is stale (delete the file).",
				keyPath,
			)
		}
		fmt.Fprintln(os.Stderr, "auto: claim token present; running claim")
		runClaim([]string{
			"--api=" + apiURL,
			"--token=" + token,
			"--key=" + keyPath,
			"--id-file=" + idFile,
			"--start-after",
		})
		return
	}

	// Branch 3: discovery flag → knock on /api/scanners/discover
	// and wait for an admin to approve in the UI.
	if isTruthy(os.Getenv("AKASHIC_DISCOVER")) {
		if keyExists {
			log.Fatalf(
				"auto: $AKASHIC_DISCOVER is set but %s already exists. "+
					"A previous claim/discover left a key but no id. Either "+
					"delete the file to start fresh or set $SCANNER_ID to its "+
					"existing scanner UUID.",
				keyPath,
			)
		}
		fmt.Fprintln(os.Stderr, "auto: discover mode; running discover")
		runDiscover([]string{
			"--api=" + apiURL,
			"--key=" + keyPath,
			"--id-file=" + idFile,
			"--start-after",
		})
		return
	}

	// Nothing matched — print a friendly diagnosis instead of just
	// dying. This is the path most people hit on first run.
	fmt.Fprintf(os.Stderr, `auto: not enough configuration to start.

You can pick any of these to bring this scanner online:

  1. Generate a join token in the Akashic UI (Settings → Scanners)
     and re-run with:   AKASHIC_CLAIM_TOKEN=akcl_… docker compose up scanner

  2. Enable discovery in the UI (Settings → Scanners → Discovery toggle)
     and re-run with:   AKASHIC_DISCOVER=1 docker compose up scanner
     The container's stderr will show a pairing code; approve it in the UI.

  3. If you already have a scanner key on disk, set $SCANNER_ID and ensure
     %s exists, then re-run.

State on this run:
  AKASHIC_API_URL    = %s
  AKASHIC_KEY_PATH   = %s   (exists: %t)
  AKASHIC_ID_FILE    = %s   (id read: %q)
  AKASHIC_CLAIM_TOKEN = (unset)
  AKASHIC_DISCOVER   = %q
  SCANNER_ID env     = %q
`,
		keyPath, apiURL, keyPath, keyExists, idFile, readScannerID(idFile),
		os.Getenv("AKASHIC_DISCOVER"), os.Getenv("SCANNER_ID"),
	)
	os.Exit(78) // EX_CONFIG
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func readScannerID(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func isTruthy(v string) bool {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "1", "true", "yes", "on":
		return true
	}
	return false
}
