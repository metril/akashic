package connector

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"sync/atomic"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// SharePointConfig is the connection_config shape for a SharePoint
// document-library source. Same OAuth-shaped pattern as OneDrive —
// access_token is minted at scan-lease time from the source's
// connected SourceOAuthCredential row (the Microsoft provider).
//
// SiteID is required: the colon-separated triple Graph uses to
// address a site (``hostname,site-collection-id,site-id``). The user
// can paste it from Graph Explorer or from the result of
// ``GET /sites/{hostname}:/sites/{site-name}``.
//
// DriveID is optional — empty means "the site's default document
// library". Sites with multiple libraries set this to the specific
// drive id.
//
// ItemID is optional — empty means "walk the drive root".
type SharePointConfig struct {
	AccessToken string
	SiteID      string
	DriveID     string
	ItemID      string
}

// SharePointConnector walks a SharePoint document library subtree via
// Microsoft Graph v1.0. The DriveItem and Permission shapes are
// identical to OneDrive — we reuse the ``driveItem`` / ``driveItemPermission``
// types and the ``buildOneDriveEntry`` / ``buildOneDriveACL`` mappers
// from onedrive.go. Only the endpoint URL prefix differs.
//
// Paths synthesise as ``/<site-or-drive-name>/<folder>/...``. Like
// OneDrive, names are unique per parent so no Drive-style collision
// suffix is needed.
type SharePointConnector struct {
	cfg                *SharePointConfig
	httpClient         *http.Client
	refreshAccessToken func(ctx context.Context) (string, error)
	currentToken       atomic.Value // string

	// Resolved at Connect; used by Walk's path-display root.
	rootDisplayName string
}

func NewSharePointConnector(cfg *SharePointConfig) *SharePointConnector {
	c := &SharePointConnector{
		cfg:        cfg,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	c.currentToken.Store(cfg.AccessToken)
	return c
}

func (c *SharePointConnector) SetRefreshCallback(fn func(ctx context.Context) (string, error)) {
	c.refreshAccessToken = fn
}

func (c *SharePointConnector) Type() string { return "sharepoint" }

// driveBase returns the Graph URL prefix for this connector's site +
// drive selection. Examples:
//
//	"/sites/<site>/drive"           — default library
//	"/sites/<site>/drives/<drive>"  — explicit library
func (c *SharePointConnector) driveBase() string {
	if c.cfg.DriveID != "" {
		return "/sites/" + c.cfg.SiteID + "/drives/" + c.cfg.DriveID
	}
	return "/sites/" + c.cfg.SiteID + "/drive"
}

func (c *SharePointConnector) graphURL(suffix string) string {
	return "https://graph.microsoft.com/v1.0" + c.driveBase() + suffix
}

// Connect verifies the access token + site id are usable. We hit
// ``/sites/{site-id}`` (not the drive endpoint) so a missing-drive
// error doesn't mask a missing-site one.
func (c *SharePointConnector) Connect(ctx context.Context) error {
	if c.cfg == nil || c.cfg.AccessToken == "" {
		return errors.New("sharepoint: missing access_token in connection_config (no OAuth credential connected to source)")
	}
	if c.cfg.SiteID == "" {
		return errors.New("sharepoint: missing site_id in connection_config")
	}
	body, err := c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/sites/"+c.cfg.SiteID+
			"?$select=id,displayName,name,webUrl",
		nil)
	if err != nil {
		return fmt.Errorf("sharepoint: /sites/{id} failed: %w", err)
	}
	defer body.Close()
	var site struct {
		DisplayName string `json:"displayName"`
		Name        string `json:"name"`
	}
	if err := json.NewDecoder(body).Decode(&site); err != nil {
		return fmt.Errorf("sharepoint: site decode: %w", err)
	}
	c.rootDisplayName = site.DisplayName
	if c.rootDisplayName == "" {
		c.rootDisplayName = site.Name
	}
	if c.rootDisplayName == "" {
		c.rootDisplayName = "SharePoint"
	}
	return nil
}

func (c *SharePointConnector) Close() error { return nil }

func (c *SharePointConnector) Walk(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fullScan bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	stats := walker.WalkStats{}

	rootID := c.cfg.ItemID
	if rootID == "" {
		rootID = "root"
	}
	rootName := c.rootDisplayName
	if rootName == "" {
		rootName = "SharePoint"
	}
	if c.cfg.ItemID != "" {
		// When scoped to a non-root item, the user expects the
		// item's own name as the root display segment.
		name, err := c.fetchItemName(ctx, c.cfg.ItemID)
		if err == nil && name != "" {
			rootName = name
		}
	}
	rootPath := "/" + rootName

	if err := fn(&models.EntryRecord{
		Path:     rootPath,
		Name:     rootName,
		Kind:     "directory",
		NativeID: c.cfg.ItemID,
	}); err != nil {
		return stats, err
	}

	type queued struct {
		id   string
		path string
	}
	queue := []queued{{id: rootID, path: rootPath}}

	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		if ctx.Err() != nil {
			return stats, ctx.Err()
		}
		err := c.listChildren(ctx, cur.id, func(child driveItem) error {
			childPath := path.Join(cur.path, child.Name)
			if matchExcludes(excludePatterns, childPath) {
				return nil
			}
			rec := buildOneDriveEntry(child, childPath)
			if !computeHash {
				rec.ContentHash = ""
			}
			if perms, err := c.fetchPermissions(ctx, child.ID); err == nil {
				rec.Acl = buildOneDriveACL(perms)
			} else if !rec.IsDir() {
				stats.InaccessibleFiles++
			}
			if err := fn(rec); err != nil {
				return err
			}
			if rec.IsDir() {
				queue = append(queue, queued{id: child.ID, path: childPath})
			}
			return nil
		})
		if err != nil {
			stats.InaccessibleDirs++
		}
	}
	return stats, nil
}

func (c *SharePointConnector) WalkShallow(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	rootID := c.cfg.ItemID
	if rootID == "" {
		rootID = "root"
	}
	rootName := c.rootDisplayName
	if rootName == "" {
		rootName = "SharePoint"
	}
	rootPath := "/" + rootName
	if err := fn(&models.EntryRecord{
		Path:     rootPath,
		Name:     rootName,
		Kind:     "directory",
		NativeID: c.cfg.ItemID,
	}); err != nil {
		return nil, err
	}
	subdirs := []string{}
	err := c.listChildren(ctx, rootID, func(child driveItem) error {
		childPath := path.Join(rootPath, child.Name)
		if matchExcludes(excludePatterns, childPath) {
			return nil
		}
		rec := buildOneDriveEntry(child, childPath)
		if !computeHash {
			rec.ContentHash = ""
		}
		if rec.IsDir() {
			subdirs = append(subdirs, childPath)
			return nil
		}
		if perms, err := c.fetchPermissions(ctx, child.ID); err == nil {
			rec.Acl = buildOneDriveACL(perms)
		}
		return fn(rec)
	})
	return subdirs, err
}

func (c *SharePointConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	id, err := c.resolveItemIDByPath(ctx, p)
	if err != nil {
		return nil, err
	}
	return c.do(ctx, "GET",
		c.graphURL("/items/"+url.PathEscape(id)+"/content"),
		nil)
}

func (c *SharePointConnector) Delete(ctx context.Context, p string) error {
	return errors.New("sharepoint: delete not supported (use SharePoint directly)")
}

// ── Internal Graph helpers (sharepoint flavour of onedrive's) ─────────────

func (c *SharePointConnector) listChildren(
	ctx context.Context,
	itemID string,
	cb func(driveItem) error,
) error {
	endpoint := c.graphURL("/items/" + url.PathEscape(itemID) +
		"/children?$top=200&$select=" + oneDriveSelect)
	for endpoint != "" {
		body, err := c.do(ctx, "GET", endpoint, nil)
		if err != nil {
			return err
		}
		var page driveItemListPage
		err = json.NewDecoder(body).Decode(&page)
		body.Close()
		if err != nil {
			return fmt.Errorf("sharepoint: children decode: %w", err)
		}
		for i := range page.Value {
			if err := cb(page.Value[i]); err != nil {
				return err
			}
		}
		endpoint = page.NextLink
	}
	return nil
}

func (c *SharePointConnector) fetchItemName(ctx context.Context, id string) (string, error) {
	body, err := c.do(ctx, "GET",
		c.graphURL("/items/"+url.PathEscape(id)+"?$select=name"),
		nil)
	if err != nil {
		return "", err
	}
	defer body.Close()
	var item driveItem
	if err := json.NewDecoder(body).Decode(&item); err != nil {
		return "", err
	}
	return item.Name, nil
}

func (c *SharePointConnector) fetchPermissions(
	ctx context.Context, itemID string,
) ([]driveItemPermission, error) {
	body, err := c.do(ctx, "GET",
		c.graphURL("/items/"+url.PathEscape(itemID)+"/permissions"),
		nil)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	var page driveItemPermissionPage
	if err := json.NewDecoder(body).Decode(&page); err != nil {
		return nil, err
	}
	return page.Value, nil
}

func (c *SharePointConnector) resolveItemIDByPath(
	ctx context.Context, p string,
) (string, error) {
	clean := strings.Trim(p, "/")
	parts := strings.SplitN(clean, "/", 2)
	if len(parts) < 2 {
		return "root", nil
	}
	subpath := parts[1]
	body, err := c.do(ctx, "GET",
		c.graphURL("/root:/"+
			strings.ReplaceAll(url.PathEscape(subpath), "%2F", "/")+
			"?$select=id"),
		nil)
	if err != nil {
		return "", err
	}
	defer body.Close()
	var item driveItem
	if err := json.NewDecoder(body).Decode(&item); err != nil {
		return "", err
	}
	return item.ID, nil
}

func (c *SharePointConnector) do(ctx context.Context, method, url string, body io.Reader) (io.ReadCloser, error) {
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return nil, err
	}
	tok, _ := c.currentToken.Load().(string)
	req.Header.Set("Authorization", "Bearer "+tok)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized && c.refreshAccessToken != nil {
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		fresh, refreshErr := c.refreshAccessToken(ctx)
		if refreshErr != nil {
			return nil, fmt.Errorf("sharepoint: 401 and refresh failed: %w", refreshErr)
		}
		c.currentToken.Store(fresh)
		req2, err := http.NewRequestWithContext(ctx, method, url, body)
		if err != nil {
			return nil, err
		}
		req2.Header.Set("Authorization", "Bearer "+fresh)
		resp, err = c.httpClient.Do(req2)
		if err != nil {
			return nil, err
		}
	}
	if resp.StatusCode >= 400 {
		buf, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, fmt.Errorf(
			"sharepoint: %s %s -> %d: %s",
			method, url, resp.StatusCode,
			strings.TrimSpace(string(buf)),
		)
	}
	return resp.Body, nil
}
