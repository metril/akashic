package connector

import (
	"context"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"cloud.google.com/go/storage"
	"google.golang.org/api/iterator"
	"google.golang.org/api/option"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// GCSConnector indexes a Google Cloud Storage bucket. v0.10.0 / Tier 2
// PR 3 — completes the Tier 2 object-store family (S3 + Azure Blob +
// GCS).
//
// Hostless source type: bucket + auth fields all on the source's
// connection_config. Two auth modes:
//
//   - **service_account_json**: paste the contents of a service account
//     JSON key file. Most common path for service-to-service auth
//     against a project the akashic operator controls.
//   - **application_default**: Application Default Credentials. Picks
//     up GKE workload identity, env-var-pointed-to JSON
//     (GOOGLE_APPLICATION_CREDENTIALS), or `gcloud auth
//     application-default login` creds. The recommended production
//     path — no inline secret to rotate.
//
// HMAC keys (S3-compatible interoperability access keys) are NOT
// implemented as a first-class GCS auth mode. Users who can only get
// HMAC creds should add a *S3* source with provider preset "Other"
// and endpoint `https://storage.googleapis.com` instead — the
// existing S3 connector handles the XML API correctly. This keeps
// the GCS connector focused on the JSON API where it has access to
// richer object metadata.
//
// Path synthesis: GCS uses the same flat-with-prefix namespace as S3
// and Azure Blob. Walk yields one EntryRecord per object with the
// full key as the path; WalkShallow uses delimiter="/" to enumerate
// "subdirectories" without recursing.
type GCSConnector struct {
	bucket             string
	prefix             string
	authMode           string
	serviceAccountJSON string

	client *storage.Client
}

func NewGCSConnector(bucket, prefix, authMode, serviceAccountJSON string) *GCSConnector {
	return &GCSConnector{
		bucket:             bucket,
		prefix:             prefix,
		authMode:           authMode,
		serviceAccountJSON: serviceAccountJSON,
	}
}

func (c *GCSConnector) Type() string { return "gcs" }

func (c *GCSConnector) Connect(ctx context.Context) error {
	if c.bucket == "" {
		return fmt.Errorf("gcs: bucket required")
	}
	mode := strings.ToLower(strings.TrimSpace(c.authMode))
	if mode == "" {
		// Implicit default — pick the mode that matches whichever
		// credential is populated.
		if c.serviceAccountJSON != "" {
			mode = "service_account_json"
		} else {
			mode = "application_default"
		}
	}

	var client *storage.Client
	var err error
	switch mode {
	case "service_account_json":
		if c.serviceAccountJSON == "" {
			return fmt.Errorf("gcs: service_account_json required for auth_mode=service_account_json")
		}
		client, err = storage.NewClient(ctx, option.WithCredentialsJSON([]byte(c.serviceAccountJSON)))
		if err != nil {
			return fmt.Errorf("gcs: new client (service account json): %w", err)
		}
	case "application_default":
		// google.NewClient with no options → ADC chain (workload
		// identity → GOOGLE_APPLICATION_CREDENTIALS env var → user
		// gcloud creds). On a misconfigured host this errors here
		// rather than at first read, surfacing a clearer message.
		client, err = storage.NewClient(ctx)
		if err != nil {
			return fmt.Errorf("gcs: new client (application default): %w", err)
		}
	default:
		return fmt.Errorf("gcs: unsupported auth_mode %q", mode)
	}
	c.client = client

	// Smoke-test bucket access. Mirrors S3's HeadBucket and Azure's
	// container.GetProperties: fails fast on missing perms or wrong
	// bucket without paying the per-page list cost.
	if _, err := c.client.Bucket(c.bucket).Attrs(ctx); err != nil {
		return fmt.Errorf("gcs: bucket %q: %w", c.bucket, err)
	}
	return nil
}

func (c *GCSConnector) Walk(
	ctx context.Context, prefix string, excludePatterns []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	var stats walker.WalkStats
	if c.client == nil {
		return stats, fmt.Errorf("gcs: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}
	listPrefix := joinPrefix(c.prefix, strings.TrimPrefix(prefix, "/"))
	it := c.client.Bucket(c.bucket).Objects(ctx, &storage.Query{Prefix: listPrefix})
	for {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		obj, err := it.Next()
		if errors.Is(err, iterator.Done) {
			break
		}
		if err != nil {
			return stats, fmt.Errorf("gcs: list: %w", err)
		}
		name := obj.Name
		if pathSegmentsExcluded(name, excludeSet) {
			continue
		}
		entry := buildGCSEntry(obj, name)
		if err := fn(entry); err != nil {
			return stats, err
		}
	}
	return stats, nil
}

// WalkShallow lists the immediate children of `prefix` without
// recursing — implements the ShallowWalker interface.
func (c *GCSConnector) WalkShallow(
	ctx context.Context, prefix string, excludePatterns []string, _ bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	if c.client == nil {
		return nil, fmt.Errorf("gcs: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}
	listPrefix := joinPrefix(c.prefix, strings.TrimPrefix(prefix, "/"))
	if listPrefix != "" && !strings.HasSuffix(listPrefix, "/") {
		listPrefix += "/"
	}
	var subdirs []string
	it := c.client.Bucket(c.bucket).Objects(ctx, &storage.Query{
		Prefix:    listPrefix,
		Delimiter: "/",
	})
	for {
		if err := ctx.Err(); err != nil {
			return subdirs, err
		}
		obj, err := it.Next()
		if errors.Is(err, iterator.Done) {
			break
		}
		if err != nil {
			return subdirs, fmt.Errorf("gcs: list shallow: %w", err)
		}
		// Subdirectory equivalents: when a Delimiter-bounded list
		// turns up keys whose name matches the delimiter shape, the
		// SDK surfaces them as `Prefix` rather than `Name`.
		if obj.Prefix != "" {
			rel := strings.TrimPrefix(obj.Prefix, listPrefix)
			rel = strings.TrimSuffix(rel, "/")
			if rel == "" || excludeSet[strings.ToLower(rel)] {
				continue
			}
			subdirs = append(subdirs, rel)
			continue
		}
		// File-equivalents at this level. obj.Name carries the full
		// path (listPrefix + filename); base-name dedup mirrors S3's
		// treatment of the "current directory" placeholder object.
		name := obj.Name
		base := filepath.Base(name)
		if name == listPrefix || excludeSet[strings.ToLower(base)] {
			continue
		}
		entry := buildGCSEntry(obj, name)
		if err := fn(entry); err != nil {
			return subdirs, err
		}
	}
	return subdirs, nil
}

func (c *GCSConnector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	if c.client == nil {
		return nil, fmt.Errorf("gcs: not connected")
	}
	r, err := c.client.Bucket(c.bucket).Object(strings.TrimPrefix(path, "/")).NewReader(ctx)
	if err != nil {
		return nil, err
	}
	return r, nil
}

func (c *GCSConnector) Delete(ctx context.Context, path string) error {
	if c.client == nil {
		return fmt.Errorf("gcs: not connected")
	}
	return c.client.Bucket(c.bucket).Object(strings.TrimPrefix(path, "/")).Delete(ctx)
}

func (c *GCSConnector) Close() error {
	if c.client != nil {
		return c.client.Close()
	}
	return nil
}

// ----- helpers -----

// joinPrefix combines the connector-level prefix with a per-call
// prefix. Either may be empty; the result has no leading slash and a
// single intermediate slash when both halves are non-empty. Used by
// Walk and WalkShallow so a connector configured with a fixed prefix
// (e.g., "data/") still honours per-call subtree scoping.
func joinPrefix(staticPrefix, perCall string) string {
	staticPrefix = strings.Trim(staticPrefix, "/")
	perCall = strings.Trim(perCall, "/")
	switch {
	case staticPrefix == "" && perCall == "":
		return ""
	case staticPrefix == "":
		return perCall
	case perCall == "":
		return staticPrefix
	}
	return staticPrefix + "/" + perCall
}

// buildGCSEntry maps a GCS ObjectAttrs onto akashic's EntryRecord.
// MD5 is preferred over CRC32C for content_hash because the existing
// dedup pipeline expects a string of arbitrary opaque bytes; MD5
// hex is a stable cross-store representation. CRC32C-only objects
// (uploads via the JSON API can omit MD5) fall back to a synthetic
// hash derived from generation + size so a re-upload is still a
// distinct content row.
func buildGCSEntry(obj *storage.ObjectAttrs, name string) *models.EntryRecord {
	entry := &models.EntryRecord{
		Path: name,
		Name: filepath.Base(name),
		Kind: "file",
	}
	size := obj.Size
	entry.SizeBytes = &size
	if !obj.Updated.IsZero() {
		t := obj.Updated
		entry.ModifiedAt = &t
	}
	switch {
	case len(obj.MD5) > 0:
		entry.ContentHash = fmt.Sprintf("md5:%x", obj.MD5)
	case obj.CRC32C != 0:
		entry.ContentHash = fmt.Sprintf("crc32c:%08x:gen:%d:size:%d", obj.CRC32C, obj.Generation, obj.Size)
	}
	if ext := filepath.Ext(entry.Name); ext != "" {
		entry.Extension = strings.TrimPrefix(ext, ".")
	}
	if obj.ContentType != "" {
		entry.MimeType = obj.ContentType
	}
	return entry
}
