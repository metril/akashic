package connector

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strings"

	"github.com/Azure/azure-sdk-for-go/sdk/azidentity"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob"
	"github.com/Azure/azure-sdk-for-go/sdk/storage/azblob/container"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// AzureBlobConnector indexes a single container in an Azure Blob
// Storage account. v0.9.0 / Tier 2 PR 2.
//
// Hostless source type: account_name + container + auth fields all
// live on the source's connection_config. The wide-vs-deep tradeoff
// (one Host = one storage account, many containers as sources) was
// considered but punted to a follow-up; users with multiple
// containers per account fill the form multiple times.
//
// Three auth modes:
//
//   - **account_key**: Shared Key auth. The account access key (Azure
//     portal → Storage account → Access keys → key1.value). Most
//     common path for one-off setups, but rotates poorly — Azure
//     recommends moving production scans to azure_ad.
//   - **sas_token**: Shared Access Signature. A pre-signed query
//     string, scoped to a container or account, with bounded lifetime.
//     Cleanest fit when the akashic operator and the Azure tenant
//     don't share an identity provider. The token may be passed
//     with or without the leading `?`; the connector normalises.
//   - **azure_ad**: DefaultAzureCredential. Picks up workload
//     identity, managed identity, environment vars, or `az login`
//     creds in that order. The recommended production path —
//     scanner pods running under an AKS workload identity get
//     credentials free with no secret to rotate.
//
// Path synthesis: Azure Blob's namespace is flat-with-prefix like S3.
// Walk emits the full blob name as the path; WalkShallow uses the
// `/` delimiter to enumerate "subdirectories" without recursing.
type AzureBlobConnector struct {
	accountName    string
	container      string
	authMode       string
	accountKey     string
	sasToken       string
	endpointSuffix string

	client *azblob.Client
}

func NewAzureBlobConnector(accountName, containerName, authMode, accountKey, sasToken, endpointSuffix string) *AzureBlobConnector {
	if endpointSuffix == "" {
		endpointSuffix = "core.windows.net"
	}
	return &AzureBlobConnector{
		accountName:    accountName,
		container:      containerName,
		authMode:       authMode,
		accountKey:     accountKey,
		sasToken:       sasToken,
		endpointSuffix: endpointSuffix,
	}
}

func (c *AzureBlobConnector) Type() string { return "azureblob" }

func (c *AzureBlobConnector) Connect(ctx context.Context) error {
	if c.accountName == "" {
		return fmt.Errorf("azureblob: account_name required")
	}
	if c.container == "" {
		return fmt.Errorf("azureblob: container required")
	}
	serviceURL := fmt.Sprintf("https://%s.blob.%s/", c.accountName, c.endpointSuffix)
	mode := strings.ToLower(strings.TrimSpace(c.authMode))
	if mode == "" {
		// Empty auth_mode is the v0.9.0 implicit default. Pick the
		// mode that matches whichever credential is populated; if
		// none, fall through to azure_ad which has no inline secret.
		switch {
		case c.accountKey != "":
			mode = "account_key"
		case c.sasToken != "":
			mode = "sas_token"
		default:
			mode = "azure_ad"
		}
	}
	switch mode {
	case "account_key":
		if c.accountKey == "" {
			return fmt.Errorf("azureblob: account_key required for auth_mode=account_key")
		}
		cred, err := azblob.NewSharedKeyCredential(c.accountName, c.accountKey)
		if err != nil {
			return fmt.Errorf("azureblob: shared key: %w", err)
		}
		client, err := azblob.NewClientWithSharedKeyCredential(serviceURL, cred, nil)
		if err != nil {
			return fmt.Errorf("azureblob: new client: %w", err)
		}
		c.client = client
	case "sas_token":
		if c.sasToken == "" {
			return fmt.Errorf("azureblob: sas_token required for auth_mode=sas_token")
		}
		// Normalise leading "?". The Azure portal copies SAS strings
		// with the "?" prefix; passing them through with the prefix
		// builds a malformed URL ("https://acct.blob.../?...?...").
		token := strings.TrimPrefix(c.sasToken, "?")
		urlWithSAS := serviceURL + "?" + token
		client, err := azblob.NewClientWithNoCredential(urlWithSAS, nil)
		if err != nil {
			return fmt.Errorf("azureblob: new client (sas): %w", err)
		}
		c.client = client
	case "azure_ad":
		cred, err := azidentity.NewDefaultAzureCredential(nil)
		if err != nil {
			return fmt.Errorf("azureblob: default azure credential: %w", err)
		}
		client, err := azblob.NewClient(serviceURL, cred, nil)
		if err != nil {
			return fmt.Errorf("azureblob: new client (aad): %w", err)
		}
		c.client = client
	default:
		return fmt.Errorf("azureblob: unsupported auth_mode %q", mode)
	}

	// Smoke-test container access — fails fast if the credential is
	// good but the container doesn't exist or this principal lacks
	// list permissions. Cheaper than starting a Walk and discovering
	// the same thing on the first ListBlobs call.
	containerClient := c.client.ServiceClient().NewContainerClient(c.container)
	if _, err := containerClient.GetProperties(ctx, nil); err != nil {
		return fmt.Errorf("azureblob: container %q: %w", c.container, err)
	}
	return nil
}

func (c *AzureBlobConnector) Walk(
	ctx context.Context, prefix string, excludePatterns []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	var stats walker.WalkStats
	if c.client == nil {
		return stats, fmt.Errorf("azureblob: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	prefixPtr := normalisePrefix(prefix)
	pager := c.client.NewListBlobsFlatPager(c.container, &azblob.ListBlobsFlatOptions{
		Prefix: prefixPtr,
	})
	for pager.More() {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		page, err := pager.NextPage(ctx)
		if err != nil {
			return stats, fmt.Errorf("azureblob: list: %w", err)
		}
		for _, blob := range page.Segment.BlobItems {
			if err := ctx.Err(); err != nil {
				return stats, err
			}
			if blob == nil || blob.Name == nil {
				continue
			}
			name := *blob.Name
			if pathSegmentsExcluded(name, excludeSet) {
				continue
			}
			entry := buildAzureBlobEntry(blob, name)
			if err := fn(entry); err != nil {
				return stats, err
			}
		}
	}
	return stats, nil
}

// WalkShallow lists the immediate children of `prefix` without
// recursing — implements the ShallowWalker interface so the agent
// can split work into per-subdirectory units.
func (c *AzureBlobConnector) WalkShallow(
	ctx context.Context, prefix string, excludePatterns []string, _ bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	if c.client == nil {
		return nil, fmt.Errorf("azureblob: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	listPrefix := strings.TrimPrefix(prefix, "/")
	if listPrefix != "" && !strings.HasSuffix(listPrefix, "/") {
		listPrefix += "/"
	}
	// `NewListBlobsHierarchyPager` is on the container.Client, not on
	// azblob.Client. Resolve through ServiceClient → NewContainerClient
	// rather than constructing a fresh container.Client (which would
	// lose the auth credentials baked into the parent client).
	containerClient := c.client.ServiceClient().NewContainerClient(c.container)
	pager := containerClient.NewListBlobsHierarchyPager("/", &container.ListBlobsHierarchyOptions{
		Prefix: stringPtr(listPrefix),
	})
	var subdirs []string
	for pager.More() {
		if err := ctx.Err(); err != nil {
			return subdirs, err
		}
		page, err := pager.NextPage(ctx)
		if err != nil {
			return subdirs, fmt.Errorf("azureblob: list shallow: %w", err)
		}
		// Subdirectory equivalents — BlobPrefixes carries every key
		// prefix that has at least one blob beneath it.
		for _, p := range page.Segment.BlobPrefixes {
			if p == nil || p.Name == nil {
				continue
			}
			full := *p.Name
			rel := strings.TrimPrefix(full, listPrefix)
			rel = strings.TrimSuffix(rel, "/")
			if rel == "" || excludeSet[strings.ToLower(rel)] {
				continue
			}
			subdirs = append(subdirs, rel)
		}
		// File-equivalents at this level — blobs whose name doesn't
		// contain a further "/" past the prefix.
		for _, blob := range page.Segment.BlobItems {
			if blob == nil || blob.Name == nil {
				continue
			}
			name := *blob.Name
			base := filepath.Base(name)
			if excludeSet[strings.ToLower(base)] {
				continue
			}
			entry := buildAzureBlobEntry(blob, name)
			if err := fn(entry); err != nil {
				return subdirs, err
			}
		}
	}
	return subdirs, nil
}

func (c *AzureBlobConnector) ReadFile(ctx context.Context, path string) (io.ReadCloser, error) {
	if c.client == nil {
		return nil, fmt.Errorf("azureblob: not connected")
	}
	out, err := c.client.DownloadStream(ctx, c.container, strings.TrimPrefix(path, "/"), nil)
	if err != nil {
		return nil, err
	}
	return out.Body, nil
}

func (c *AzureBlobConnector) Delete(ctx context.Context, path string) error {
	if c.client == nil {
		return fmt.Errorf("azureblob: not connected")
	}
	_, err := c.client.DeleteBlob(ctx, c.container, strings.TrimPrefix(path, "/"), nil)
	return err
}

func (c *AzureBlobConnector) Close() error { return nil }

// ----- helpers -----

// stringPtr returns &s. Used to satisfy the Azure SDK's pointer-string
// option fields; a literal `*string` from a non-addressable expression
// isn't valid Go.
func stringPtr(s string) *string { return &s }

// normalisePrefix converts a leading-slash path into the Azure Blob
// "key prefix" shape (no leading slash). Returns nil when the prefix
// is "/" or "" so the SDK treats the listing as unfiltered.
func normalisePrefix(p string) *string {
	p = strings.TrimPrefix(p, "/")
	if p == "" {
		return nil
	}
	return &p
}

// pathSegmentsExcluded mirrors S3's segment-level exclude semantics —
// any "/"-separated component matching the lowercase exclude set hides
// the blob.
func pathSegmentsExcluded(name string, set map[string]bool) bool {
	if len(set) == 0 {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if set[strings.ToLower(part)] {
			return true
		}
	}
	return false
}

// buildAzureBlobEntry maps an Azure SDK BlobItem onto akashic's
// EntryRecord shape. Pulled out of Walk / WalkShallow so the two
// share the metadata-mapping logic.
func buildAzureBlobEntry(blob *container.BlobItem, name string) *models.EntryRecord {
	entry := &models.EntryRecord{
		Path: name,
		Name: filepath.Base(name),
		Kind: "file",
	}
	if blob.Properties != nil {
		if blob.Properties.ContentLength != nil {
			s := *blob.Properties.ContentLength
			entry.SizeBytes = &s
		}
		if blob.Properties.LastModified != nil {
			t := *blob.Properties.LastModified
			entry.ModifiedAt = &t
		}
		if blob.Properties.ContentMD5 != nil && len(blob.Properties.ContentMD5) > 0 {
			// MD5 is the cheapest content hash Azure supplies inline.
			// We pretend it's a sha-prefixed value so the existing
			// dedup pipeline (which expects a content_hash string)
			// handles it the same as S3's ETag-derived hashes.
			entry.ContentHash = fmt.Sprintf("md5:%x", blob.Properties.ContentMD5)
		} else if blob.Properties.ETag != nil {
			entry.ContentHash = strings.Trim(string(*blob.Properties.ETag), "\"")
		}
	}
	if ext := filepath.Ext(entry.Name); ext != "" {
		entry.Extension = strings.TrimPrefix(ext, ".")
	}
	return entry
}

