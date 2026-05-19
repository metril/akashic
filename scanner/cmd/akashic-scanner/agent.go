package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/agent"
)

// Build-time version. Overridden via -ldflags -X main.Version=… by
// CI; defaults to "dev" for local builds.
var Version = "dev"

// runAgent is the entry point for `akashic-scanner agent …`. It
// blocks until SIGINT/SIGTERM cancels the context.
func runAgent(args []string) {
	fs := flag.NewFlagSet("agent", flag.ExitOnError)
	apiURL := fs.String("api", "", "Akashic API base URL (e.g. https://api.example.com)")
	scannerID := fs.String("scanner-id", "", "Scanner UUID, as registered in the api")
	keyPath := fs.String("key", "", "Path to the Ed25519 private key (PEM, PKCS8)")
	leasePoll := fs.Duration("lease-poll", 5*time.Second, "Lease-poll interval (jittered ±20%%)")
	tikaURL := fs.String("tika-url", "", "Apache Tika base URL for content extraction (e.g. http://tika:9998); empty disables document extraction")
	extractWorkers := fs.Int("extract-workers", 0, "Content-extraction worker pool size (0 = default)")
	maxConcurrentUnits := fs.Int("max-concurrent-units", 0, "Work units walked in parallel on one scan (0 = default 1)")
	if err := fs.Parse(args); err != nil {
		log.Fatalf("agent flags: %v", err)
	}
	if *apiURL == "" || *scannerID == "" || *keyPath == "" {
		fs.Usage()
		log.Fatal("--api, --scanner-id, --key are required")
	}

	// v0.30.0 — env fallbacks so the compose `auto` entrypoint (which
	// re-execs `agent` with only --api/--scanner-id/--key but inherits
	// the environment) can still configure extraction.
	resolvedTikaURL := *tikaURL
	if resolvedTikaURL == "" {
		resolvedTikaURL = strings.TrimRight(os.Getenv("AKASHIC_TIKA_URL"), "/")
	}
	resolvedExtractWorkers := *extractWorkers
	if resolvedExtractWorkers == 0 {
		if n, err := strconv.Atoi(os.Getenv("AKASHIC_EXTRACT_WORKERS")); err == nil && n > 0 {
			resolvedExtractWorkers = n
		}
	}
	// v0.35.0 — per-scanner unit concurrency. Same flag-or-env pattern;
	// defaults to 1 (one unit at a time, today's behaviour).
	resolvedMaxConcurrentUnits := *maxConcurrentUnits
	if resolvedMaxConcurrentUnits == 0 {
		if n, err := strconv.Atoi(os.Getenv("AKASHIC_MAX_CONCURRENT_UNITS")); err == nil && n > 0 {
			resolvedMaxConcurrentUnits = n
		}
	}
	if resolvedMaxConcurrentUnits < 1 {
		resolvedMaxConcurrentUnits = 1
	}

	hostname, _ := os.Hostname()

	// v0.30.2 — announce the running build up front so `docker logs`
	// makes the deployed version verifiable on every scanner host
	// (in-place image upgrades otherwise leave no startup trace).
	log.Printf("akashic-scanner %s starting (scanner-id=%s, host=%s, api=%s)",
		Version, *scannerID, hostname, *apiURL)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// SIGTERM (docker stop) and SIGINT (Ctrl-C) both unwind the lease
	// loop cleanly. The reporter inside runLeasedScan watches scanCtx
	// (a child of this ctx) so a leased scan in flight is cancelled
	// at the same time.
	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		s := <-sigs
		log.Printf("received signal %s; shutting down", s)
		cancel()
	}()

	cfg := agent.Config{
		APIBase:            *apiURL,
		ScannerID:          *scannerID,
		KeyPath:            *keyPath,
		LeasePoll:          *leasePoll,
		Hostname:           hostname,
		Version:            Version,
		TikaURL:            resolvedTikaURL,
		ExtractWorkers:     resolvedExtractWorkers,
		MaxConcurrentUnits: resolvedMaxConcurrentUnits,
	}
	if err := agent.Run(ctx, cfg); err != nil {
		log.Fatalf("agent: %v", err)
	}
}
