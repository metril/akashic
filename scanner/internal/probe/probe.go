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
	case "azureblob":
		return runAzureBlob(ctx, connConfig)
	case "gcs":
		return runGCS(ctx, connConfig)
	case "webdav":
		return runWebDAV(ctx, connConfig)
	case "gdrive":
		return runGDrive(ctx, connConfig)
	case "onedrive":
		return runOneDrive(ctx, connConfig)
	case "sharepoint":
		return runSharePoint(ctx, connConfig)
	case "dropbox":
		return runDropbox(ctx, connConfig)
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
	// v0.8.1 — honour the explicit path_style override when set.
	// nil = auto (path-style when endpoint is set, virtual-hosted
	// otherwise). MinIO wants true; Wasabi/B2 want false even with
	// their endpoint set.
	pathStyle := endpoint != ""
	if v, ok := c["path_style"]; ok {
		if b, ok := v.(bool); ok {
			pathStyle = b
		}
	}
	client := s3.NewFromConfig(cfg, func(o *s3.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
		o.UsePathStyle = pathStyle
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

// runWebDAV validates a WebDAV source by issuing the connector's
// Connect — which sends a `PROPFIND Depth: 0` against the source
// URL. 401/403 → "auth"; 405 (server doesn't speak WebDAV at this
// path) → "list"; transport / DNS / TLS → "connect".
func runWebDAV(ctx context.Context, c map[string]any) Result {
	rawURL := strings.TrimSpace(str(c, "url"))
	username := str(c, "username")
	password := str(c, "password")
	if rawURL == "" {
		return Result{OK: false, Step: "config", Error: "url required"}
	}
	verify := true
	if v, ok := c["tls_verify"]; ok {
		if b, ok := v.(bool); ok {
			verify = b
		}
	}
	conn := connector.NewWebDAVConnector(rawURL, username, password, verify)
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "auth rejected"):
			return Result{OK: false, Step: "auth", Error: msg}
		case strings.Contains(msg, "PROPFIND not allowed"),
			strings.Contains(msg, "resource not found"):
			return Result{OK: false, Step: "list", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}

// runGCS validates a GCS source by issuing the connector's Connect.
// Connect builds the storage client (validating the JSON key or
// triggering the ADC chain) and then calls Bucket.Attrs to confirm
// the bucket is readable. SDK errors map to the standard taxonomy:
// permission / 401 / 403 → "auth"; bucket-doesnt-exist → "list";
// transport / DNS / TLS → "connect".
func runGCS(ctx context.Context, c map[string]any) Result {
	bucket := strings.TrimSpace(str(c, "bucket"))
	authMode := strings.TrimSpace(str(c, "auth_mode"))
	if bucket == "" {
		return Result{OK: false, Step: "config", Error: "bucket required"}
	}
	conn := connector.NewGCSConnector(
		bucket,
		str(c, "prefix"),
		authMode,
		str(c, "service_account_json"),
	)
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "service_account_json required"),
			strings.Contains(msg, "unsupported auth_mode"),
			strings.Contains(strings.ToLower(msg), "permission denied"),
			strings.Contains(strings.ToLower(msg), "unauthorized"),
			strings.Contains(strings.ToLower(msg), "invalid_grant"),
			strings.Contains(strings.ToLower(msg), "credentials"),
			strings.Contains(strings.ToLower(msg), "could not find default"):
			return Result{OK: false, Step: "auth", Error: msg}
		case strings.Contains(strings.ToLower(msg), "bucket "),
			strings.Contains(strings.ToLower(msg), "notfound"):
			return Result{OK: false, Step: "list", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}

// runAzureBlob validates an Azure Blob Storage source by issuing
// the connector's Connect — which validates the auth chain and
// probes container.GetProperties. Auth-rejected (the SDK surfaces
// 403 / AuthenticationFailed) → "auth"; unreachable / DNS / TLS
// → "connect"; other 4xx → "list".
func runAzureBlob(ctx context.Context, c map[string]any) Result {
	accountName := strings.TrimSpace(str(c, "account_name"))
	containerName := strings.TrimSpace(str(c, "container"))
	authMode := strings.TrimSpace(str(c, "auth_mode"))
	if accountName == "" {
		return Result{OK: false, Step: "config", Error: "account_name required"}
	}
	if containerName == "" {
		return Result{OK: false, Step: "config", Error: "container required"}
	}
	conn := connector.NewAzureBlobConnector(
		accountName,
		containerName,
		authMode,
		str(c, "account_key"),
		str(c, "sas_token"),
		str(c, "endpoint_suffix"),
	)
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "AuthenticationFailed"),
			strings.Contains(msg, "AuthorizationFailure"),
			strings.Contains(msg, "InvalidAuthenticationInfo"),
			strings.Contains(msg, "account_key required"),
			strings.Contains(msg, "sas_token required"),
			strings.Contains(msg, "default azure credential"):
			return Result{OK: false, Step: "auth", Error: msg}
		case strings.Contains(msg, "ContainerNotFound"),
			strings.Contains(msg, "container "):
			return Result{OK: false, Step: "list", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
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

// runGDrive validates a Google Drive source by calling about.get with
// the access token in connection_config. The token is minted by the
// API at probe-request time (test_gdrive in services/source_tester.py)
// from the source's SourceOAuthCredential row, just like a scan-time
// lease.
//
// Failure shapes mapped: missing token → "config"; 401 → "auth";
// network/DNS/TLS → "connect".
func runGDrive(ctx context.Context, c map[string]any) Result {
	access := str(c, "access_token")
	if access == "" {
		return Result{OK: false, Step: "config", Error: "no access_token (no OAuth credential connected)"}
	}
	conn := connector.NewGDriveConnector(&connector.GDriveConfig{
		AccessToken: access,
		FolderID:    str(c, "folder_id"),
	})
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "401"):
			return Result{OK: false, Step: "auth", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}

// runOneDrive validates a OneDrive (or work/school OneDrive) source
// via Microsoft Graph's /me endpoint. Same auth/connect classification
// as runGDrive — token minted by the API from the connected
// SourceOAuthCredential row.
func runOneDrive(ctx context.Context, c map[string]any) Result {
	access := str(c, "access_token")
	if access == "" {
		return Result{OK: false, Step: "config", Error: "no access_token (no OAuth credential connected)"}
	}
	conn := connector.NewOneDriveConnector(&connector.OneDriveConfig{
		AccessToken: access,
		ItemID:      str(c, "item_id"),
	})
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "401"):
			return Result{OK: false, Step: "auth", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}

// runSharePoint validates a SharePoint document library source via
// Graph's ``/sites/{site-id}`` endpoint. Site missing → "config";
// 401 → "auth"; other errors → "connect" so the user gets the same
// step-classified feedback as the other Graph-backed sources.
func runSharePoint(ctx context.Context, c map[string]any) Result {
	access := str(c, "access_token")
	if access == "" {
		return Result{OK: false, Step: "config", Error: "no access_token (no OAuth credential connected)"}
	}
	siteID := str(c, "site_id")
	if siteID == "" {
		return Result{OK: false, Step: "config", Error: "site_id is required for SharePoint sources"}
	}
	conn := connector.NewSharePointConnector(&connector.SharePointConfig{
		AccessToken: access,
		SiteID:      siteID,
		DriveID:     str(c, "drive_id"),
		ItemID:      str(c, "item_id"),
	})
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "401"):
			return Result{OK: false, Step: "auth", Error: msg}
		case strings.Contains(msg, "404"):
			return Result{OK: false, Step: "config", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}

// runDropbox validates a Dropbox source by calling
// /2/users/get_current_account with the access token in
// connection_config. Same router-injected access_token flow as the
// other OAuth-shaped types. Failure shapes mapped: missing token
// → "config"; 401 → "auth"; otherwise → "connect".
func runDropbox(ctx context.Context, c map[string]any) Result {
	access := str(c, "access_token")
	if access == "" {
		return Result{OK: false, Step: "config", Error: "no access_token (no OAuth credential connected)"}
	}
	conn := connector.NewDropboxConnector(&connector.DropboxConfig{
		AccessToken: access,
		Path:        str(c, "path"),
	})
	probeCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	defer conn.Close()
	if err := conn.Connect(probeCtx); err != nil {
		msg := err.Error()
		switch {
		case strings.Contains(msg, "401"):
			return Result{OK: false, Step: "auth", Error: msg}
		default:
			return Result{OK: false, Step: "connect", Error: msg}
		}
	}
	return Result{OK: true}
}
