package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/config"
	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/observe"
	"github.com/akashic-project/akashic/scanner/internal/scanner"
)

func main() {
	// Subcommand dispatch — short-circuit the normal scan flow when the
	// first arg names a subcommand.
	if len(os.Args) > 1 {
		switch os.Args[1] {
		case "resolve-groups":
			runResolveGroups(os.Args[2:])
			return
		case "resolve-sids":
			runResolveSids(os.Args[2:])
			return
		case "test-connection":
			runTestConnection(os.Args[2:])
			return
		case "fetch":
			runFetch(os.Args[2:])
			return
		}
	}

	sourceID := flag.String("source-id", "", "Source ID to scan")
	scanID := flag.String("scan-id", "", "Scan ID for this run")
	sourceType := flag.String("type", "local", "Source type: local, ssh, smb, nfs, s3")
	root := flag.String("root", "", "Root path to scan")
	host := flag.String("host", "", "Remote host (for ssh, smb, s3)")
	port := flag.Int("port", 0, "Remote port")
	username := flag.String("user", "", "Username")
	password := flag.String("pass", "", "Password")
	keyPath := flag.String("key", "", "SSH key path")
	keyPassphrase := flag.String("key-passphrase", "", "SSH key passphrase (for passphrase-protected private keys)")
	knownHosts := flag.String("known-hosts", "", "Path to SSH known_hosts file for host key verification")
	share := flag.String("share", "", "SMB share name")
	bucket := flag.String("bucket", "", "S3 bucket name")
	region := flag.String("region", "us-east-1", "S3 region")
	endpoint := flag.String("endpoint", "", "S3 endpoint URL")
	excludes := flag.String("exclude", ".git,node_modules,__pycache__,.DS_Store,Thumbs.db", "Comma-separated exclude patterns")
	fullScan := flag.Bool("full", false, "Full scan (hash all files)")
	batchSize := flag.Int("batch-size", 1000, "Files per batch")
	lastScanStr := flag.String("last-scan", "", "RFC3339 timestamp of last scan; enables incremental mode (only re-hashes changed files)")
	prewalk := flag.Bool("prewalk", false, "Run a count-only pass first to estimate total files for ETA (first-scan only — adds I/O)")
	noObserve := flag.Bool("no-observe", false, "Disable live progress reporting and stderr relay (run scanner standalone)")

	flag.Parse()

	// Suppress "declared and not used" for flags only needed by specific connectors.
	_ = host
	_ = port
	_ = username
	_ = password
	_ = keyPath
	_ = keyPassphrase
	_ = knownHosts
	_ = share
	_ = bucket
	_ = region
	_ = endpoint

	var lastScanTime *time.Time
	if *lastScanStr != "" {
		t, err := time.Parse(time.RFC3339, *lastScanStr)
		if err != nil {
			log.Fatalf("invalid --last-scan timestamp (expected RFC3339): %v", err)
		}
		lastScanTime = &t
	}

	if *sourceID == "" || *root == "" {
		fmt.Fprintln(os.Stderr, "required: -source-id and -root")
		flag.Usage()
		os.Exit(1)
	}

	cfg := config.Load()

	var conn connector.Connector
	switch *sourceType {
	case "local":
		conn = connector.NewLocalConnector()
	case "nfs":
		conn = connector.NewNFSConnector()
	case "ssh":
		p := *port
		if p == 0 {
			p = 22
		}
		conn = connector.NewSSHConnector(*host, p, *username, *password, *keyPath, *keyPassphrase, *knownHosts)
	case "smb":
		p := *port
		if p == 0 {
			p = 445
		}
		conn = connector.NewSMBConnector(*host, p, *username, *password, *share)
	case "s3":
		conn = connector.NewS3Connector(*endpoint, *bucket, *region, *username, *password)
	default:
		log.Fatalf("unknown source type: %s", *sourceType)
	}

	var excludePatterns []string
	if *excludes != "" {
		excludePatterns = strings.Split(*excludes, ",")
	}

	apiClient := client.New(cfg.APIUrl, cfg.APIKey)

	sid := *scanID
	if sid == "" {
		sid = uuid.New().String()
	}

	ctx, cancelCtx := context.WithCancel(context.Background())
	defer cancelCtx()

	// Phase 1 — observability. Disabled when --no-observe (standalone /
	// CI runs) or when no API key is configured (the heartbeat / log POSTs
	// would 401 in a tight loop anyway).
	var (
		reporter      *observe.Reporter
		state         *observe.State
		stopReporter  func()
		stopStderrTee func()
	)
	if !*noObserve && cfg.APIKey != "" {
		state = observe.NewState()
		reporter = observe.New(cfg.APIUrl, cfg.APIKey, sid, state)
		// User-cancel: a 409 from heartbeat means the user pressed Stop
		// in the UI. Cancel the outer context so the connector and
		// walker both unwind. scanner.Run returns ctx.Err() which we
		// log as a non-fatal exit below.
		reporter.SetUserCancel(cancelCtx)
		reporter.Start(ctx)
		// Stderr relay AFTER Start so the goroutines are draining when the
		// pipe replaces os.Stderr — avoids losing the very first chunk
		// to a not-yet-running drain.
		if cleanup, err := reporter.StartStderrRelay(ctx); err == nil {
			stopStderrTee = cleanup
		}
		stopReporter = reporter.Stop
	}

	s := scanner.New(apiClient, conn, scanner.Options{
		SourceID:        *sourceID,
		ScanID:          sid,
		Root:            *root,
		BatchSize:       *batchSize,
		Hash:            *fullScan,
		ExcludePatterns: excludePatterns,
		LastScanTime:    lastScanTime,
		Prewalk:         *prewalk,
		Reporter:        reporter,
		State:           state,
	})

	result, err := s.Run(ctx)

	if stopStderrTee != nil {
		stopStderrTee()
	}
	if stopReporter != nil {
		stopReporter()
	}

	if err != nil {
		// Cancellation isn't a crash: the API has already marked the
		// scan as cancelled and the source as online. Exit 0 with a
		// log line so any orchestrator (cron, HA, etc.) doesn't treat
		// "user pressed Stop" as a transient failure to retry.
		if errors.Is(err, context.Canceled) || ctx.Err() != nil {
			log.Printf("scan cancelled")
			return
		}
		log.Fatalf("scan failed: %v", err)
	}

	fmt.Printf("Scan complete: %d files, %d directories, %d batches sent\n",
		result.FilesFound, result.DirsFound, result.BatchesSent)
}
