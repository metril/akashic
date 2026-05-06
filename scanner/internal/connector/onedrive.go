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

// OneDriveConfig is the connection_config shape the api hands the
// scanner for a OneDrive (personal or work/school) source.
//
// ItemID is optional — when empty, the connector walks the user's
// drive root. When set, only that item's subtree is walked. Useful
// for users who want to scope indexing to a single project folder.
type OneDriveConfig struct {
	AccessToken string
	ItemID      string
}

// OneDriveConnector walks a OneDrive subtree via the Microsoft Graph
// v1.0 API. Same shape as gdrive.go: BFS by item ID, plain net/http +
// JSON (no msgraph-sdk-go dep — the surface we use is small enough).
//
// Path synthesis: Graph's DriveItem carries a ``parentReference.path``
// of the form ``/drive/root:/Documents/Reports`` — strip the
// ``/drive/root:`` prefix to get akashic's display path. OneDrive
// enforces unique sibling names so we don't need Drive's
// `` (id)`` collision suffix.
//
// Hash: ``file.hashes.sha1Hash`` is the most universally available;
// emit ``sha1:<hex>`` so content_hash plays nicely with the existing
// prefix-tagged hash vocabulary. Fall back to ``file.hashes.quickXorHash``
// (consumer OneDrive only) prefixed ``quickxor:`` when sha1 is absent.
type OneDriveConnector struct {
	cfg                *OneDriveConfig
	httpClient         *http.Client
	refreshAccessToken func(ctx context.Context) (string, error)
	currentToken       atomic.Value // string
}

func NewOneDriveConnector(cfg *OneDriveConfig) *OneDriveConnector {
	c := &OneDriveConnector{
		cfg:        cfg,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	c.currentToken.Store(cfg.AccessToken)
	return c
}

func (c *OneDriveConnector) SetRefreshCallback(fn func(ctx context.Context) (string, error)) {
	c.refreshAccessToken = fn
}

func (c *OneDriveConnector) Type() string { return "onedrive" }

// Connect verifies the access token works against Graph and surfaces
// the connected user's email for diagnostics. /me is the cheapest
// authenticated endpoint that exercises the same auth path the walk
// will use.
func (c *OneDriveConnector) Connect(ctx context.Context) error {
	if c.cfg == nil || c.cfg.AccessToken == "" {
		return errors.New("onedrive: missing access_token in connection_config (no OAuth credential connected to source)")
	}
	body, err := c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/me?$select=displayName,mail,userPrincipalName",
		nil)
	if err != nil {
		return fmt.Errorf("onedrive: /me failed: %w", err)
	}
	defer body.Close()
	var me struct {
		Mail              string `json:"mail"`
		UserPrincipalName string `json:"userPrincipalName"`
	}
	if err := json.NewDecoder(body).Decode(&me); err != nil {
		return fmt.Errorf("onedrive: /me decode: %w", err)
	}
	return nil
}

func (c *OneDriveConnector) Close() error { return nil }

func (c *OneDriveConnector) Walk(
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
		rootID = "root" // Graph alias for the drive root.
	}
	rootName := "OneDrive"
	if c.cfg.ItemID != "" {
		name, err := c.fetchItemName(ctx, c.cfg.ItemID)
		if err != nil {
			return stats, fmt.Errorf("onedrive: resolve item %q: %w", c.cfg.ItemID, err)
		}
		rootName = name
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
			// Permissions are not returned with the children listing —
			// pull them per-item. One Graph call per file is the
			// not-cheap part of OneDrive scans; in a future pass we
			// could parallelise via worker pool. For v0.15.0 we keep
			// it serial.
			if !rec.IsDir() {
				if perms, err := c.fetchPermissions(ctx, child.ID); err == nil {
					rec.Acl = buildOneDriveACL(perms)
				} else {
					stats.InaccessibleFiles++
				}
			} else {
				if perms, err := c.fetchPermissions(ctx, child.ID); err == nil {
					rec.Acl = buildOneDriveACL(perms)
				}
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

func (c *OneDriveConnector) WalkShallow(
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
	rootName := "OneDrive"
	if c.cfg.ItemID != "" {
		name, err := c.fetchItemName(ctx, c.cfg.ItemID)
		if err != nil {
			return nil, err
		}
		rootName = name
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

// ReadFile downloads the binary content for an item. Graph hands back
// a 302 redirect to a pre-signed URL on the storage backend — our
// http.Client follows redirects by default, so we just stream the body.
func (c *OneDriveConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	id, err := c.resolveItemIDByPath(ctx, p)
	if err != nil {
		return nil, err
	}
	return c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/me/drive/items/"+url.PathEscape(id)+"/content",
		nil)
}

// Delete is a no-op on OneDrive — duplicate-delete on cloud-drive
// sources isn't supported in v0.15.0. Returning an explicit error is
// friendlier than a silent success.
func (c *OneDriveConnector) Delete(ctx context.Context, p string) error {
	return errors.New("onedrive: delete not supported (use OneDrive directly)")
}

// ── Internal Graph REST helpers ──────────────────────────────────────────

// driveItem is the subset of a Graph DriveItem we use.
type driveItem struct {
	ID                   string `json:"id"`
	Name                 string `json:"name"`
	Size                 int64  `json:"size"`
	ETag                 string `json:"eTag"`
	CreatedDateTime      string `json:"createdDateTime"`
	LastModifiedDateTime string `json:"lastModifiedDateTime"`
	WebURL               string `json:"webUrl"`
	File                 *struct {
		MimeType string `json:"mimeType"`
		Hashes   struct {
			SHA1Hash      string `json:"sha1Hash"`
			SHA256Hash    string `json:"sha256Hash"`
			QuickXorHash  string `json:"quickXorHash"`
			Crc32Hash     string `json:"crc32Hash"`
		} `json:"hashes"`
	} `json:"file,omitempty"`
	Folder *struct {
		ChildCount int `json:"childCount"`
	} `json:"folder,omitempty"`
	ParentReference *struct {
		ID   string `json:"id"`
		Path string `json:"path"`
	} `json:"parentReference,omitempty"`
}

type driveItemListPage struct {
	Value    []driveItem `json:"value"`
	NextLink string      `json:"@odata.nextLink"`
}

const oneDriveSelect = "id,name,size,eTag,createdDateTime,lastModifiedDateTime,webUrl,file,folder,parentReference"

func (c *OneDriveConnector) listChildren(
	ctx context.Context,
	itemID string,
	cb func(driveItem) error,
) error {
	endpoint := "https://graph.microsoft.com/v1.0/me/drive/items/" +
		url.PathEscape(itemID) + "/children?$top=200&$select=" + oneDriveSelect
	for endpoint != "" {
		body, err := c.do(ctx, "GET", endpoint, nil)
		if err != nil {
			return err
		}
		var page driveItemListPage
		err = json.NewDecoder(body).Decode(&page)
		body.Close()
		if err != nil {
			return fmt.Errorf("onedrive: children decode: %w", err)
		}
		for i := range page.Value {
			if err := cb(page.Value[i]); err != nil {
				return err
			}
		}
		endpoint = page.NextLink // empty when no more pages
	}
	return nil
}

func (c *OneDriveConnector) fetchItemName(ctx context.Context, id string) (string, error) {
	body, err := c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/me/drive/items/"+url.PathEscape(id)+"?$select=name",
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

// driveItemPermissionPage is the shape of /items/{id}/permissions.
type driveItemPermissionPage struct {
	Value    []driveItemPermission `json:"value"`
	NextLink string                `json:"@odata.nextLink"`
}

type driveItemPermission struct {
	ID            string                       `json:"id"`
	Roles         []string                     `json:"roles"`
	GrantedToV2   *driveItemGrantedToIdentitySet `json:"grantedToV2,omitempty"`
	Link          *driveItemSharingLink        `json:"link,omitempty"`
	InheritedFrom *struct {
		ID   string `json:"id"`
		Path string `json:"path"`
	} `json:"inheritedFrom,omitempty"`
}

type driveItemGrantedToIdentitySet struct {
	User *struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"displayName"`
	} `json:"user,omitempty"`
	Group *struct {
		ID          string `json:"id"`
		DisplayName string `json:"displayName"`
	} `json:"group,omitempty"`
}

type driveItemSharingLink struct {
	Type  string `json:"type"`  // view | edit | embed
	Scope string `json:"scope"` // anonymous | users | organization
	WebURL string `json:"webUrl"`
}

func (c *OneDriveConnector) fetchPermissions(
	ctx context.Context, itemID string,
) ([]driveItemPermission, error) {
	body, err := c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/me/drive/items/"+url.PathEscape(itemID)+"/permissions",
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

func (c *OneDriveConnector) resolveItemIDByPath(
	ctx context.Context, p string,
) (string, error) {
	// Use Graph's path-based addressing: /me/drive/root:/<path>
	// Returns the item id directly.
	clean := strings.Trim(p, "/")
	parts := strings.SplitN(clean, "/", 2)
	if len(parts) < 2 {
		// Just the drive root segment; return root
		return "root", nil
	}
	subpath := parts[1] // strip the synthetic "OneDrive" segment
	body, err := c.do(ctx, "GET",
		"https://graph.microsoft.com/v1.0/me/drive/root:/"+
			strings.ReplaceAll(url.PathEscape(subpath), "%2F", "/")+"?$select=id",
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

// do issues an authenticated request with one 401-driven refresh+retry.
func (c *OneDriveConnector) do(ctx context.Context, method, url string, body io.Reader) (io.ReadCloser, error) {
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
			return nil, fmt.Errorf("onedrive: 401 and refresh failed: %w", refreshErr)
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
			"onedrive: %s %s -> %d: %s",
			method, url, resp.StatusCode,
			strings.TrimSpace(string(buf)),
		)
	}
	return resp.Body, nil
}

// ── Wire-shape mappers ────────────────────────────────────────────────────

func buildOneDriveEntry(item driveItem, displayPath string) *models.EntryRecord {
	rec := &models.EntryRecord{
		Path:     displayPath,
		Name:     item.Name,
		NativeID: item.ID,
	}
	if item.Folder != nil {
		rec.Kind = "directory"
	} else {
		rec.Kind = "file"
		ext := path.Ext(item.Name)
		if ext != "" {
			rec.Extension = strings.TrimPrefix(ext, ".")
		}
	}
	if item.File != nil {
		rec.MimeType = item.File.MimeType
		// Prefer SHA-1 (most universally available across consumer +
		// business OneDrive). Fall back to QuickXorHash (consumer-
		// only, but stable enough for dedup).
		switch {
		case item.File.Hashes.SHA1Hash != "":
			rec.ContentHash = "sha1:" + strings.ToLower(item.File.Hashes.SHA1Hash)
		case item.File.Hashes.SHA256Hash != "":
			rec.ContentHash = "sha256:" + strings.ToLower(item.File.Hashes.SHA256Hash)
		case item.File.Hashes.QuickXorHash != "":
			rec.ContentHash = "quickxor:" + item.File.Hashes.QuickXorHash
		}
	}
	if item.Size > 0 {
		s := item.Size
		rec.SizeBytes = &s
	}
	if item.LastModifiedDateTime != "" {
		if t, err := time.Parse(time.RFC3339, item.LastModifiedDateTime); err == nil {
			rec.ModifiedAt = &t
		}
	}
	if item.CreatedDateTime != "" {
		if t, err := time.Parse(time.RFC3339, item.CreatedDateTime); err == nil {
			rec.CreatedAt = &t
		}
	}
	return rec
}

// buildOneDriveACL maps a DriveItem's permissions[] into the
// cloud_drive ACL discriminator. Graph permission shapes:
//
//  - User grant:  ``grantedToV2.user.{id,email,displayName}`` + roles[]
//  - Group grant: ``grantedToV2.group.{id,displayName}`` + roles[]
//  - Sharing link: ``link.{type,scope}`` — scope=anonymous → "anyone"
//                  with link, scope=organization → "domain", scope=users
//                  is per-named-user (covered by the user grant in the
//                  same permission entry).
//
// Roles map: ``read`` → reader, ``write`` → writer, ``owner`` → owner.
// Graph doesn't have commenter/file_organizer; we leave those Drive-
// specific.
func buildOneDriveACL(perms []driveItemPermission) *models.ACL {
	if len(perms) == 0 {
		return nil
	}
	grants := make([]models.CloudDriveGrant, 0, len(perms))
	for _, p := range perms {
		role := mapOneDriveRole(p.Roles)
		if role == "" {
			continue
		}
		var principal models.CloudDrivePrincipal
		var link *models.CloudDriveLink
		if p.Link != nil && p.Link.Scope == "anonymous" {
			principal = models.CloudDrivePrincipal{Type: "anyone", ID: "anyone"}
			link = &models.CloudDriveLink{ID: p.ID, Scope: "anyone"}
		} else if p.Link != nil && p.Link.Scope == "organization" {
			principal = models.CloudDrivePrincipal{Type: "domain", ID: "organization"}
			link = &models.CloudDriveLink{ID: p.ID, Scope: "domain"}
		} else if p.GrantedToV2 != nil && p.GrantedToV2.User != nil {
			u := p.GrantedToV2.User
			principal = models.CloudDrivePrincipal{
				Type:  "user",
				ID:    u.ID,
				Email: u.Email,
				Name:  u.DisplayName,
			}
		} else if p.GrantedToV2 != nil && p.GrantedToV2.Group != nil {
			g := p.GrantedToV2.Group
			principal = models.CloudDrivePrincipal{
				Type: "group",
				ID:   g.ID,
				Name: g.DisplayName,
			}
		} else {
			continue // unknown shape — skip rather than emit a malformed grant
		}
		grant := models.CloudDriveGrant{
			Principal: principal,
			Role:      role,
			Link:      link,
		}
		if p.InheritedFrom != nil {
			grant.Inherited = true
			grant.InheritedFromID = p.InheritedFrom.ID
			grant.InheritedFromPath = p.InheritedFrom.Path
		}
		grants = append(grants, grant)
	}
	if len(grants) == 0 {
		return nil
	}
	return &models.ACL{Type: "cloud_drive", CloudDriveGrants: grants}
}

func mapOneDriveRole(roles []string) string {
	// Roles can be a list — Graph returns ["read"], ["write"], ["owner"]
	// or sometimes ["read", "write"]. Take the strongest role.
	hasOwner, hasWrite, hasRead := false, false, false
	for _, r := range roles {
		switch r {
		case "owner":
			hasOwner = true
		case "write":
			hasWrite = true
		case "read":
			hasRead = true
		}
	}
	switch {
	case hasOwner:
		return "owner"
	case hasWrite:
		return "writer"
	case hasRead:
		return "reader"
	}
	return ""
}
