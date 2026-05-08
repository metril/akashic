package connector

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// v0.22.0 — concurrent /permissions fetches per directory's children
// page. Microsoft Graph allows ~10 req/sec/user/app and Box (where the
// same pattern applies in box.go) tolerates 1000/min, so 8 workers
// stays well clear at typical 100-200ms latency. Tunable in the future
// via a config field if needed; not exposed yet because the win is
// already 5-8x and there's no in-the-wild rate-limit complaint to
// react to.
const onedrivePermWorkers = 8

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

		// Phase 1: buffer the children of this directory. listChildren
		// pages internally; we collect everything to disk first so the
		// permissions fetches can fan out concurrently below.
		var children []driveItem
		err := c.listChildren(ctx, cur.id, func(child driveItem) error {
			children = append(children, child)
			return nil
		})
		if err != nil {
			stats.InaccessibleDirs++
			continue
		}

		// Pre-filter against the exclude list so we don't waste Graph
		// quota on items we're not going to surface anyway. Track the
		// already-built childPath next to the item to avoid re-joining.
		type kept struct {
			item driveItem
			path string
		}
		keepers := make([]kept, 0, len(children))
		for _, ch := range children {
			cp := path.Join(cur.path, ch.Name)
			if matchExcludes(excludePatterns, cp) {
				continue
			}
			keepers = append(keepers, kept{item: ch, path: cp})
		}

		// Phase 2: fan out permissions. Bounded by `onedrivePermWorkers`
		// so we don't blow Graph's per-user rate budget; results parked
		// in an index-aligned slice so the next phase can iterate in
		// the original API response order (callers see deterministic
		// emit order regardless of which goroutine finished first).
		type permResult struct {
			perms []driveItemPermission
			err   error
		}
		results := make([]permResult, len(keepers))
		if len(keepers) > 0 {
			sem := make(chan struct{}, onedrivePermWorkers)
			var wg sync.WaitGroup
			cancelled := false
			for i, k := range keepers {
				select {
				case <-ctx.Done():
					cancelled = true
				default:
				}
				if cancelled {
					break
				}
				wg.Add(1)
				sem <- struct{}{}
				go func(i int, id string) {
					defer wg.Done()
					defer func() { <-sem }()
					p, err := c.fetchPermissions(ctx, id)
					results[i] = permResult{perms: p, err: err}
				}(i, k.item.ID)
			}
			// Drain in-flight goroutines before returning so the
			// caller can't race with a still-writing background fetch.
			wg.Wait()
			if cancelled {
				return stats, ctx.Err()
			}
		}

		// Phase 3: emit in order. ACL on success; bump
		// InaccessibleFiles only for files (directories with denied
		// perms still surface for tree-walk completeness, mirroring
		// the pre-v0.22.0 behaviour).
		for i, k := range keepers {
			rec := buildOneDriveEntry(k.item, k.path)
			if !computeHash {
				rec.ContentHash = ""
			}
			pr := results[i]
			if pr.err == nil {
				rec.Acl = buildOneDriveACL(pr.perms)
			} else if !rec.IsDir() {
				stats.InaccessibleFiles++
			}
			if err := fn(rec); err != nil {
				return stats, err
			}
			if rec.IsDir() {
				queue = append(queue, queued{id: k.item.ID, path: k.path})
			}
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
	// Same buffer-and-fanout pattern as Walk above. WalkShallow only
	// emits files (directories are returned as paths so the caller can
	// recurse selectively), so the permission fetches all hit
	// non-directory items.
	subdirs := []string{}
	var children []driveItem
	if err := c.listChildren(ctx, rootID, func(child driveItem) error {
		children = append(children, child)
		return nil
	}); err != nil {
		return nil, err
	}

	type kept struct {
		item driveItem
		path string
	}
	var fileKeepers []kept
	for _, ch := range children {
		cp := path.Join(rootPath, ch.Name)
		if matchExcludes(excludePatterns, cp) {
			continue
		}
		// Surface dirs immediately; only files need a permissions fetch.
		if ch.Folder != nil {
			subdirs = append(subdirs, cp)
			continue
		}
		fileKeepers = append(fileKeepers, kept{item: ch, path: cp})
	}

	type permResult struct {
		perms []driveItemPermission
		err   error
	}
	results := make([]permResult, len(fileKeepers))
	if len(fileKeepers) > 0 {
		sem := make(chan struct{}, onedrivePermWorkers)
		var wg sync.WaitGroup
		cancelled := false
		for i, k := range fileKeepers {
			select {
			case <-ctx.Done():
				cancelled = true
			default:
			}
			if cancelled {
				break
			}
			wg.Add(1)
			sem <- struct{}{}
			go func(i int, id string) {
				defer wg.Done()
				defer func() { <-sem }()
				p, err := c.fetchPermissions(ctx, id)
				results[i] = permResult{perms: p, err: err}
			}(i, k.item.ID)
		}
		wg.Wait()
		if cancelled {
			return subdirs, ctx.Err()
		}
	}

	for i, k := range fileKeepers {
		rec := buildOneDriveEntry(k.item, k.path)
		if !computeHash {
			rec.ContentHash = ""
		}
		if results[i].err == nil {
			rec.Acl = buildOneDriveACL(results[i].perms)
		}
		if err := fn(rec); err != nil {
			return subdirs, err
		}
	}
	return subdirs, nil
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
	// Graph paginates /permissions when an item has more grants than
	// the default page (200). Pre-fix this read only the first page
	// and silently dropped any permissions beyond it (review S-I3),
	// so items shared with many individual users had truncated ACLs.
	endpoint := "https://graph.microsoft.com/v1.0/me/drive/items/" +
		url.PathEscape(itemID) + "/permissions"
	var all []driveItemPermission
	for endpoint != "" {
		body, err := c.do(ctx, "GET", endpoint, nil)
		if err != nil {
			return nil, err
		}
		var page driveItemPermissionPage
		if derr := json.NewDecoder(body).Decode(&page); derr != nil {
			body.Close()
			return nil, derr
		}
		body.Close()
		all = append(all, page.Value...)
		endpoint = page.NextLink
	}
	return all, nil
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
//
// body is taken as []byte (not io.Reader) so the retry path can
// re-construct a fresh bytes.Reader. An io.Reader would be drained
// by the first attempt and the retry would silently send an empty
// body — review S-I1.
func (c *OneDriveConnector) do(ctx context.Context, method, url string, body []byte) (io.ReadCloser, error) {
	makeReq := func(token string) (*http.Request, error) {
		var r io.Reader
		if body != nil {
			r = bytes.NewReader(body)
		}
		req, err := http.NewRequestWithContext(ctx, method, url, r)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Authorization", "Bearer "+token)
		return req, nil
	}

	tok, _ := c.currentToken.Load().(string)
	req, err := makeReq(tok)
	if err != nil {
		return nil, err
	}
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
		req2, err := makeReq(fresh)
		if err != nil {
			return nil, err
		}
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
