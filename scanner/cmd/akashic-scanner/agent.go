package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
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
	if err := fs.Parse(args); err != nil {
		log.Fatalf("agent flags: %v", err)
	}
	if *apiURL == "" || *scannerID == "" || *keyPath == "" {
		fs.Usage()
		log.Fatal("--api, --scanner-id, --key are required")
	}

	hostname, _ := os.Hostname()

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
		APIBase:   *apiURL,
		ScannerID: *scannerID,
		KeyPath:   *keyPath,
		LeasePoll: *leasePoll,
		Hostname:  hostname,
		Version:   Version,
	}
	if err := agent.Run(ctx, cfg); err != nil {
		log.Fatalf("agent: %v", err)
	}
}
