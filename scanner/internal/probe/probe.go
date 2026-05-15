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
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	awsconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"

	"github.com/akashic-project/akashic/scanner/internal/connector"
	"github.com/akashic-project/akashic/scanner/internal/nfsprobe"
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
	case "webdav":
		return runWebDAV(ctx, connConfig)
	case "gdrive":
		return runGDrive(ctx, connConfig)
	case "onedrive":
		return runOneDrive(ctx, connConfig)
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
	// The local source's connection_config key is `path` (matching what
	// `connectorFromLeased` and `sourceRoot` read). Older code paths
	// also looked at `root_path`, so check both — the lease payload
	// shape has shifted over releases and we don't want a perfectly
	// reachable local source to fail the reachability poll because
	// the key name disagrees with the rest of the codebase.
	root := str(c, "path")
	if root == "" {
		root = str(c, "root_path")
	}
	if root == "" {
		return Result{OK: false, Step: "config", Error: "path required"}
	}
	info, err := os.Stat(root)
	if err != nil {
		// ENOENT, EACCES, etc. all classify as connect-level failures
		// from the api's perspective — the path isn't reachable.
		return Result{OK: false, Step: "connect", Error: err.Error()}
	}
	if !info.IsDir() {
		return Result{OK: false, Step: "config", Error: "path is not a directory"}
	}
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
	password := str(c, "password")
	// v0.29.5 — reject empty-password SMB probes. Pre-fix
	// `NTLMInitiator{User: "alice", Password: ""}` was accepted by
	// go-smb2 (the vendor only rejects empty User); some SMB servers
	// — Samba with `force user`, Windows with a null-password
	// account, allow-anonymous shares — respond to that with a fully
	// AUTHENTICATED session (not guest), so the v0.29.1 IsGuest /
	// IsAnonymous rejection never fires and `ok=true` came back for
	// credentials the user knew were wrong. The opt-out
	// `allow_empty_password: true` exists for legitimate
	// lab/anonymous-share configurations.
	if password == "" && !boolish(c, "allow_empty_password") {
		return Result{
			OK:    false,
			Step:  "config",
			Error: "password required (empty-password SMB scans are not supported; " +
				"some servers accept this as an authenticated session against a " +
				"null-password account, masking real auth failures — set " +
				"connection_config.allow_empty_password=true to explicitly opt in)",
		}
	}
	port := intish(c, "port")
	if port == 0 {
		port = 445
	}
	conn := connector.NewSMBConnector(host, port, user, password, share)
	if boolish(c, "allow_empty_password") {
		conn.SetAllowEmptyPassword(true)
	}
	if err := conn.Connect(ctx); err != nil {
		// Classify by SMBConnector's prefix taxonomy. Pre-fix every
		// Connect error mapped to step=auth, which made the v0.29.1
		// guest-rejection diagnostic harder to read against a real
		// "host unreachable" failure. Match the CLI's classifySMBError.
		step, msg := classifySMBProbeError(err)
		return Result{OK: false, Step: step, Error: msg}
	}
	defer conn.Close()
	return Result{OK: true}
}

// boolish reads a config bool with the same tolerance as intish for ints:
// accepts native bool, plus "true"/"1"/"yes" string aliases. Missing or
// any other value reads as false.
func boolish(c map[string]any, key string) bool {
	switch v := c[key].(type) {
	case bool:
		return v
	case string:
		switch strings.ToLower(strings.TrimSpace(v)) {
		case "true", "1", "yes":
			return true
		}
	}
	return false
}

// classifySMBProbeError maps an SMBConnector.Connect error to a probe
// step. The connector wraps each failure with a known prefix: "smb dial",
// "smb session", or "smb mount". Mirrors the CLI's classifySMBError so
// the agent-side and CLI-side probes report the same step for the same
// underlying failure.
func classifySMBProbeError(err error) (step, msg string) {
	s := err.Error()
	switch {
	case strings.HasPrefix(s, "smb dial"):
		return "connect", strings.TrimPrefix(s, "smb dial ")
	case strings.HasPrefix(s, "smb session"):
		return "auth", strings.TrimPrefix(s, "smb session: ")
	case strings.HasPrefix(s, "smb mount"):
		return "mount", strings.TrimPrefix(s, "smb mount ")
	default:
		return "connect", s
	}
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
	exportPath := str(c, "export_path")
	if exportPath == "" {
		// "path" is the legacy key that older configs may carry. The CLI
		// requires --export-path, so without one we'd be doing the same
		// fall-through to TCP-only the pre-v0.29.0 probe did — which is
		// the bug we're fixing. Be explicit instead.
		return Result{
			OK:    false,
			Step:  "config",
			Error: "export_path required (NFS mount path on the server)",
		}
	}

	// v0.29.0 — honest probe via the in-process nfsprobe package, the
	// same code path `akashic-scanner test-connection --type=nfs` calls
	// from the API-side source_tester. Pre-fix the in-process probe was
	// a raw `net.Dial("tcp", host:port)` which returned ok=true purely
	// from port reachability — credentials wrong, share missing, root-
	// squashed UID — all reported "reachable". A scanner reachability
	// claim must reflect whether the share is actually *scanable* with
	// the configured credentials, not just that the NFS port is open.
	authMethod := nfsprobe.AuthMethod(strings.ToLower(strings.TrimSpace(str(c, "auth_method"))))
	if authMethod == "" {
		authMethod = nfsprobe.AuthSys
	}
	auxGIDs := parseAuxGIDsAny(c["auth_aux_gids"])

	timeout := time.Duration(intish(c, "probe_timeout_seconds")) * time.Second
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	// Outer timeout matches the CLI: 3× per-RPC for sys, 5× for krb5
	// (TGS_REQ + INIT + LOOKUP serial round trips). The cancel
	// derivation already respects the caller's ctx — whichever
	// deadline fires first wins.
	mult := 3
	if authMethod == nfsprobe.AuthKrb5 || authMethod == nfsprobe.AuthKrb5Integrity || authMethod == nfsprobe.AuthKrb5Privacy {
		mult = 5
	}
	probeCtx, cancel := context.WithTimeout(ctx, time.Duration(mult)*timeout)
	defer cancel()

	res, err := nfsprobe.Probe(probeCtx, nfsprobe.ProbeOptions{
		Host:                 host,
		Port:                 uint32(port),
		ExportPath:           exportPath,
		AuthMethod:           authMethod,
		AuthUID:              uint32(intish(c, "auth_uid")),
		AuthGID:              uint32(intish(c, "auth_gid")),
		AuthAuxGIDs:          auxGIDs,
		Timeout:              timeout,
		Krb5Principal:        str(c, "krb5_principal"),
		Krb5Realm:            str(c, "krb5_realm"),
		Krb5ServicePrincipal: str(c, "krb5_service_principal"),
		Krb5KeytabPath:       str(c, "krb5_keytab_path"),
		Krb5Password:         str(c, "krb5_password"),
		Krb5ConfigPath:       str(c, "krb5_config_path"),
	})
	if err != nil {
		var pe *nfsprobe.ProbeError
		if errors.As(err, &pe) {
			return Result{OK: false, Step: string(pe.Step), Error: pe.Msg}
		}
		return Result{OK: false, Step: "connect", Error: err.Error()}
	}
	if res == nil || !res.OK {
		// nfsprobe contract says either typed error OR non-nil ok=true,
		// but be defensive — a malformed return shouldn't claim ok.
		return Result{OK: false, Step: "connect", Error: "nfsprobe returned no result"}
	}
	// A local-mount safety check — if the source was configured to
	// expect a local mount path (separate from the server-side export),
	// stat it so the scan doesn't crash later. Kept post-probe so the
	// honest auth/mount/list result takes precedence.
	if localMount := str(c, "local_mount_path"); localMount != "" {
		if _, statErr := os.Stat(localMount); statErr != nil {
			return Result{
				OK:    false,
				Step:  "list",
				Error: fmt.Sprintf("local mount path %q not accessible: %v", localMount, statErr),
			}
		}
	}
	return Result{OK: true}
}

// parseAuxGIDsAny normalises the auth_aux_gids field which may arrive
// as []any (JSON decode), []uint32 (programmatic), or a comma-string.
// Non-numeric fragments are dropped silently — input validation lives
// on the API side, this is the scanner being tolerant of shape drift.
func parseAuxGIDsAny(v any) []uint32 {
	switch t := v.(type) {
	case []uint32:
		return t
	case []any:
		out := make([]uint32, 0, len(t))
		for _, x := range t {
			switch n := x.(type) {
			case float64:
				out = append(out, uint32(n))
			case int:
				out = append(out, uint32(n))
			case int64:
				out = append(out, uint32(n))
			case string:
				if u, err := strconv.ParseUint(strings.TrimSpace(n), 10, 32); err == nil {
					out = append(out, uint32(u))
				}
			}
		}
		return out
	case string:
		parts := strings.Split(t, ",")
		out := make([]uint32, 0, len(parts))
		for _, p := range parts {
			if u, err := strconv.ParseUint(strings.TrimSpace(p), 10, 32); err == nil {
				out = append(out, uint32(u))
			}
		}
		return out
	}
	return nil
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

// runImmich validates an Immich source by issuing the connector's
// Connect — which calls /api/server-info/ping with the api_key.
// 401/403 → "auth"; transport / DNS / TLS → "connect"; missing
// fields → "config".
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

// runDropbox validates a Dropbox source by calling
// /2/users/get_current_account with the router-injected access
// token. Missing token → "config"; 401 → "auth"; other errors →
// "connect", same step-classification as the other OAuth-shaped
// providers.
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

