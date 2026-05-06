// Package probe runs in-process reachability tests against a source.
// It mirrors the per-type logic in cmd/akashic-scanner/test_connection.go
// but exposes it as a callable function so the agent's reachability poll
// loop can probe directly without forking a subprocess.
//
// The CLI subcommand `akashic-scanner test-connection` keeps the same
// step:reason error contract; the api parses it via subprocess. Both
// paths converge on the same underlying connector primitives.
package probe

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/connector"
)

// Result captures the same shape the api expects in a reachability
// report: ok=true → success, ok=false + Step/Error explain the failure.
type Result struct {
	OK    bool
	Step  string // "connect" | "auth" | "mount" | "list" | "config"
	Error string
}

// Run dispatches to the per-type probe. `connConfig` is the merged
// host+source config exactly as the api delivers it on the
// reachability/poll response.
func Run(ctx context.Context, sourceType string, connConfig map[string]any) Result {
	switch sourceType {
	case "ssh":
		return runSSH(ctx, connConfig)
	case "smb":
		return runSMB(ctx, connConfig)
	case "s3":
		return runS3(ctx, connConfig)
	case "nfs":
		return runNFS(ctx, connConfig)
	case "local":
		return runLocal(ctx, connConfig)
	case "paperless":
		return runPaperless(ctx, connConfig)
	case "immich":
		return runImmich(ctx, connConfig)
	}
	return Result{OK: false, Step: "config", Error: "unsupported source type " + sourceType}
}

func str(c map[string]any, key string) string {
	v, _ := c[key].(string)
	return v
}

func intish(c map[string]any, key string) int {
	switch v := c[key].(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	case string:
		n, _ := strconv.Atoi(v)
		return n
	}
	return 0
}

func runLocal(_ context.Context, c map[string]any) Result {
	root := str(c, "root_path")
	if root == "" {
		return Result{OK: false, Step: "config", Error: "root_path required"}
	}
	info, err := os.Stat(root)
	if err != nil {
		// ENOENT, EACCES, etc. all classify as connect-level failures
		// from the api's perspective — the path isn't reachable.
		return Result{OK: false, Step: "connect", Error: err.Error()}
	}
	if !info.IsDir() {
		return Result{OK: false, Step: "config", Error: "root_path is not a directory"}
	}
	return Result{OK: true}
}

func runSSH(ctx context.Context, c map[string]any) Result {
	host := str(c, "host")
	user := str(c, "username")
	if user == "" {
		user = str(c, "user")
	}
	if host == "" || user == "" {
		return Result{OK: false, Step: "config", Error: "host and user required"}
	}
	port := intish(c, "port")
	if port == 0 {
		port = 22
	}
	knownHosts := str(c, "known_hosts_path")
	if knownHosts == "" {
		return Result{OK: false, Step: "config", Error: "known_hosts required (strict by default)"}
	}
	conn := connector.NewSSHConnector(
		host, port, user,
		str(c, "password"),
		str(c, "key_path"),
		str(c, "key_passphrase"),
		knownHosts,
	)
	if err := conn.Connect(ctx); err != nil {
		return Result{OK: false, Step: "auth", Error: err.Error()}
	}
	defer conn.Close()
	return Result{OK: true}
}

func runSMB(ctx context.Context, c map[string]any) Result {
	host := str(c, "host")
	user := str(c, "username")
	if user == "" {
		user = str(c, "user")
	}
	share := str(c, "share")
	if host == "" || user == "" || share == "" {
		return Result{OK: false, Step: "config", Error: "host, user, share required"}
	}
	port := intish(c, "port")
	if port == 0 {
		port = 445
	}
	conn := connector.NewSMBConnector(host, port, user, str(c, "password"), share)
	if err := conn.Connect(ctx); err != nil {
		return Result{OK: false, Step: "auth", Error: err.Error()}
	}
	defer conn.Close()
	return Result{OK: true}
}

func runS3(ctx context.Context, c map[string]any) Result {
	bucket := str(c, "bucket")
	if bucket == "" {
		return Result{OK: false, Step: "config", Error: "bucket required"}
	}
	region := str(c, "region")
	if region == "" {
		region = "us-east-1"
	}
	endpoint := str(c, "endpoint")
	accessKey := str(c, "access_key_id")
	secretKey := str(c, "secret_access_key")

	probeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	cfg, err := awsconfig.LoadDefaultConfig(probeCtx,
		awsconfig.WithRegion(region),
		awsconfig.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKey, secretKey, ""),
		),
	)
	if err != nil {
		return Result{OK: false, Step: "config", Error: err.Error()}
	}
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
			o.UsePathStyle = true
		}
	})
	if _, err := client.HeadBucket(probeCtx, &s3.HeadBucketInput{Bucket: aws.String(bucket)}); err != nil {
		// HeadBucket distinguishes 403 (auth ok, perms wrong) from 404
		// (bucket missing) but both surface as "list" failures here.
		var oe interface{ ErrorCode() string }
		if errors.As(err, &oe) {
			return Result{OK: false, Step: "list", Error: fmt.Sprintf("%s: %s", oe.ErrorCode(), err.Error())}
		}
		return Result{OK: false, Step: "list", Error: err.Error()}
	}
	return Result{OK: true}
}

func runNFS(ctx context.Context, c map[string]any) Result {
	host := str(c, "host")
	if host == "" {
		return Result{OK: false, Step: "config", Error: "host required"}
	}
	port := intish(c, "port")
	if port == 0 {
		port = 2049
	}
	// TCP-level reachability is enough for the agent's reachability
	// probe — full MOUNT3/NFSv4 probes happen via the CLI subcommand
	// during pre-flight. The reachability loop runs every minute and
	// just needs to know "is the NFS service answering?".
	probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	d := net.Dialer{}
	conn, err := d.DialContext(probeCtx, "tcp", net.JoinHostPort(host, strconv.Itoa(port)))
	if err != nil {
		return Result{OK: false, Step: "connect", Error: err.Error()}
	}
	_ = conn.Close()
	return Result{OK: true}
}

// runImmich validates an Immich source by issuing the connector's
// Connect (which probes /api/server-info/ping with the api_key).
// Auth-rejected (401/403) → "auth"; transport / TLS / DNS → "connect".
// Loading the album list also exercises pagination shape, so a green
// probe is a real end-to-end go-signal for the scanner.
func runImmich(ctx context.Context, c map[string]any) Result {
	rawURL := strings.TrimSpace(str(c, "url"))
	apiKey := str(c, "api_key")
	if rawURL == "" {
		return Result{OK: false, Step: "config", Error: "url required"}
	}
	if apiKey == "" {
		return Result{OK: false, Step: "config", Error: "api_key required"}
	}
	verify := true
	if v, ok := c["tls_verify"]; ok {
		if b, ok := v.(bool); ok {
			verify = b
		}
	}
	conn := connector.NewImmichConnector(rawURL, apiKey, nil, false, verify)
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		if strings.Contains(msg, "auth rejected") {
			return Result{OK: false, Step: "auth", Error: msg}
		}
		return Result{OK: false, Step: "connect", Error: msg}
	}
	return Result{OK: true}
}

// runPaperless validates a Paperless-ngx source by performing an
// authenticated GET against /api/documents/?page_size=1. Auth-rejected
// (401/403) maps to "auth"; non-2xx maps to "list"; transport errors
// map to "connect". A green result means the URL resolves, the TLS /
// HTTP layer is healthy, the token works, and the list endpoint
// answers — i.e., everything the scanner needs to walk.
func runPaperless(ctx context.Context, c map[string]any) Result {
	rawURL := strings.TrimSpace(str(c, "url"))
	token := str(c, "api_token")
	if rawURL == "" {
		return Result{OK: false, Step: "config", Error: "url required"}
	}
	if token == "" {
		return Result{OK: false, Step: "config", Error: "api_token required"}
	}
	verify := true
	if v, ok := c["tls_verify"]; ok {
		if b, ok := v.(bool); ok {
			verify = b
		}
	}
	conn := connector.NewPaperlessConnector(rawURL, token, nil, verify)
	probeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	if err := conn.Connect(probeCtx); err != nil {
		// Connect() smoke-tests `/api/documents/?page_size=1` then
		// loads the lookup tables. The first failure surfaces
		// here, so a 401/403 from the documents endpoint shows up
		// as the documents-related error message.
		msg := err.Error()
		if strings.Contains(msg, "auth rejected") {
			return Result{OK: false, Step: "auth", Error: msg}
		}
		return Result{OK: false, Step: "connect", Error: msg}
	}
	return Result{OK: true}
}
