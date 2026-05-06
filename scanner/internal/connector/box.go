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

// BoxConfig is the connection_config shape for a Box source.
//
// FolderID is optional — empty string maps to Box's literal "0",
// which is the All Files root. Non-empty values must be Box folder
// ids (numeric strings).
//
// JWT app-auth (server-to-server, RSA-signed JWT instead of OAuth
// authorization-code) is on the roadmap; v0.18.0 ships OAuth-only.
type BoxConfig struct {
	AccessToken string
	FolderID    string
}

// BoxConnector indexes a Box account via the public REST API. Box
// uses opaque ids for everything (folder_id, file_id), so the walk
// is a BFS keyed by parent id with a synthesized display path —
// similar shape to the Drive connector.
//
// Pagination: ``/folders/{id}/items`` is offset-based (``offset`` +
// ``limit``, default limit=100; we request 200 to halve calls).
//
// Hash: each file row carries a ``sha1`` field; emit ``sha1:<hex>``
// to fit the existing prefix-tagged content_hash vocabulary.
//
// Permissions → cloud_drive ACL: per-item ``/collaborations``
// fetched after listing each child. Box roles map cleanly to the
// cloud_drive role lattice (owner → owner, editor → writer,
// viewer → reader, etc).
type BoxConnector struct {
	cfg                *BoxConfig
	httpClient         *http.Client
	refreshAccessToken func(ctx context.Context) (string, error)
	currentToken       atomic.Value // string
}

func NewBoxConnector(cfg *BoxConfig) *BoxConnector {
	c := &BoxConnector{
		cfg:        cfg,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	c.currentToken.Store(cfg.AccessToken)
	return c
}

func (c *BoxConnector) SetRefreshCallback(fn func(ctx context.Context) (string, error)) {
	c.refreshAccessToken = fn
}

func (c *BoxConnector) Type() string { return "box" }

func (c *BoxConnector) Connect(ctx context.Context) error {
	if c.cfg == nil || c.cfg.AccessToken == "" {
		return errors.New("box: missing access_token in connection_config (no OAuth credential connected to source)")
	}
	body, err := c.do(ctx, "GET",
		"https://api.box.com/2.0/users/me?fields=login,name", nil)
	if err != nil {
		return fmt.Errorf("box: users/me failed: %w", err)
	}
	defer body.Close()
	var me struct {
		Login string `json:"login"`
		Name  string `json:"name"`
	}
	if err := json.NewDecoder(body).Decode(&me); err != nil {
		return fmt.Errorf("box: users/me decode: %w", err)
	}
	return nil
}

func (c *BoxConnector) Close() error { return nil }

// rootID returns the folder id for the walk's starting point. Box
// uses the literal "0" for the All Files root; the user's FolderID
// (when set) wins.
func (c *BoxConnector) rootID() string {
	if c.cfg.FolderID != "" {
		return c.cfg.FolderID
	}
	return "0"
}

func (c *BoxConnector) Walk(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fullScan bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	stats := walker.WalkStats{}

	rootID := c.rootID()
	rootName := "All Files"
	if c.cfg.FolderID != "" {
		name, err := c.fetchFolderName(ctx, c.cfg.FolderID)
		if err != nil {
			return stats, fmt.Errorf("box: resolve folder %q: %w", c.cfg.FolderID, err)
		}
		rootName = name
	}
	rootPath := "/" + rootName

	if err := fn(&models.EntryRecord{
		Path:     rootPath,
		Name:     rootName,
		Kind:     "directory",
		NativeID: c.cfg.FolderID,
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
		err := c.listChildren(ctx, cur.id, func(child boxItem) error {
			childPath := path.Join(cur.path, child.Name)
			if matchExcludes(excludePatterns, childPath) {
				return nil
			}
			rec := buildBoxEntry(child, childPath)
			if !computeHash {
				rec.ContentHash = ""
			}
			if perms, err := c.fetchCollaborations(ctx, child.Type, child.ID); err == nil {
				rec.Acl = buildBoxACL(perms)
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

func (c *BoxConnector) WalkShallow(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	rootID := c.rootID()
	rootName := "All Files"
	if c.cfg.FolderID != "" {
		name, err := c.fetchFolderName(ctx, c.cfg.FolderID)
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
		NativeID: c.cfg.FolderID,
	}); err != nil {
		return nil, err
	}
	subdirs := []string{}
	err := c.listChildren(ctx, rootID, func(child boxItem) error {
		childPath := path.Join(rootPath, child.Name)
		if matchExcludes(excludePatterns, childPath) {
			return nil
		}
		rec := buildBoxEntry(child, childPath)
		if !computeHash {
			rec.ContentHash = ""
		}
		if rec.IsDir() {
			subdirs = append(subdirs, childPath)
			return nil
		}
		if perms, err := c.fetchCollaborations(ctx, child.Type, child.ID); err == nil {
			rec.Acl = buildBoxACL(perms)
		}
		return fn(rec)
	})
	return subdirs, err
}

// ReadFile downloads the binary content for a Box file. The content
// endpoint hands back a 302 redirect to a pre-signed download URL;
// the http.Client follows redirects by default.
func (c *BoxConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	id, err := c.resolveItemIDByPath(ctx, p)
	if err != nil {
		return nil, err
	}
	return c.do(ctx, "GET",
		"https://api.box.com/2.0/files/"+url.PathEscape(id)+"/content", nil)
}

func (c *BoxConnector) Delete(ctx context.Context, p string) error {
	return errors.New("box: delete not supported (use Box directly)")
}

// ── Internal helpers ─────────────────────────────────────────────────────

type boxItem struct {
	Type        string `json:"type"` // "file" | "folder"
	ID          string `json:"id"`
	Name        string `json:"name"`
	Size        int64  `json:"size"`
	Sha1        string `json:"sha1,omitempty"`
	ModifiedAt  string `json:"modified_at"`
	CreatedAt   string `json:"created_at"`
	Description string `json:"description,omitempty"`
	Parent      *struct {
		Type string `json:"type"`
		ID   string `json:"id"`
		Name string `json:"name"`
	} `json:"parent,omitempty"`
}

type boxItemListPage struct {
	Entries    []boxItem `json:"entries"`
	Offset     int       `json:"offset"`
	Limit      int       `json:"limit"`
	TotalCount int       `json:"total_count"`
}

const boxItemFields = "type,id,name,size,sha1,modified_at,created_at,parent"

func (c *BoxConnector) listChildren(
	ctx context.Context,
	folderID string,
	cb func(boxItem) error,
) error {
	offset := 0
	const pageSize = 200
	for {
		q := url.Values{}
		q.Set("limit", fmt.Sprintf("%d", pageSize))
		q.Set("offset", fmt.Sprintf("%d", offset))
		q.Set("fields", boxItemFields)
		body, err := c.do(ctx, "GET",
			"https://api.box.com/2.0/folders/"+url.PathEscape(folderID)+
				"/items?"+q.Encode(),
			nil)
		if err != nil {
			return err
		}
		var page boxItemListPage
		err = json.NewDecoder(body).Decode(&page)
		body.Close()
		if err != nil {
			return fmt.Errorf("box: items decode: %w", err)
		}
		for i := range page.Entries {
			if err := cb(page.Entries[i]); err != nil {
				return err
			}
		}
		offset += len(page.Entries)
		if len(page.Entries) == 0 || offset >= page.TotalCount {
			return nil
		}
	}
}

func (c *BoxConnector) fetchFolderName(ctx context.Context, id string) (string, error) {
	body, err := c.do(ctx, "GET",
		"https://api.box.com/2.0/folders/"+url.PathEscape(id)+"?fields=name",
		nil)
	if err != nil {
		return "", err
	}
	defer body.Close()
	var item boxItem
	if err := json.NewDecoder(body).Decode(&item); err != nil {
		return "", err
	}
	return item.Name, nil
}

// boxCollaboration is one /collaborations entry on a file or folder.
type boxCollaboration struct {
	Type         string `json:"type"`
	ID           string `json:"id"`
	Role         string `json:"role"`
	AccessibleBy *struct {
		Type  string `json:"type"` // "user" | "group"
		ID    string `json:"id"`
		Name  string `json:"name"`
		Login string `json:"login"` // email for users
	} `json:"accessible_by,omitempty"`
	Status string `json:"status"`
}

type boxCollaborationPage struct {
	Entries    []boxCollaboration `json:"entries"`
	TotalCount int                `json:"total_count"`
}

// fetchCollaborations pulls the collaboration list for a file or
// folder. itemType is "file" or "folder"; the endpoints differ only
// by the /files vs /folders prefix.
func (c *BoxConnector) fetchCollaborations(
	ctx context.Context, itemType, itemID string,
) ([]boxCollaboration, error) {
	prefix := "files"
	if itemType == "folder" {
		prefix = "folders"
	}
	body, err := c.do(ctx, "GET",
		"https://api.box.com/2.0/"+prefix+"/"+url.PathEscape(itemID)+
			"/collaborations?fields=role,accessible_by,status",
		nil)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	var page boxCollaborationPage
	if err := json.NewDecoder(body).Decode(&page); err != nil {
		return nil, err
	}
	return page.Entries, nil
}

// resolveItemIDByPath walks the synthesized display path one segment
// at a time (skipping the synthetic root). Box doesn't have a
// path-based addressing endpoint like Graph's /root:/<path>, so we
// listChildren our way down. Tolerable for ReadFile callers that
// operate on a handful of files.
func (c *BoxConnector) resolveItemIDByPath(ctx context.Context, p string) (string, error) {
	clean := strings.Trim(p, "/")
	if clean == "" {
		return "", fmt.Errorf("box: empty path")
	}
	parts := strings.Split(clean, "/")
	parentID := c.rootID()
	for _, seg := range parts[1:] {
		var matched *boxItem
		err := c.listChildren(ctx, parentID, func(child boxItem) error {
			if matched != nil {
				return nil
			}
			if child.Name == seg {
				ch := child
				matched = &ch
			}
			return nil
		})
		if err != nil {
			return "", err
		}
		if matched == nil {
			return "", fmt.Errorf("box: path segment %q not found under folder %q", seg, parentID)
		}
		parentID = matched.ID
	}
	return parentID, nil
}

func (c *BoxConnector) do(ctx context.Context, method, url string, body io.Reader) (io.ReadCloser, error) {
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
			return nil, fmt.Errorf("box: 401 and refresh failed: %w", refreshErr)
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
		return nil, fmt.Errorf("box: %s %s -> %d: %s",
			method, url, resp.StatusCode, strings.TrimSpace(string(buf)))
	}
	return resp.Body, nil
}

// ── Wire-shape mappers ────────────────────────────────────────────────────

func buildBoxEntry(item boxItem, displayPath string) *models.EntryRecord {
	rec := &models.EntryRecord{
		Path:     displayPath,
		Name:     item.Name,
		NativeID: item.ID,
	}
	if item.Type == "folder" {
		rec.Kind = "directory"
	} else {
		rec.Kind = "file"
		ext := path.Ext(item.Name)
		if ext != "" {
			rec.Extension = strings.TrimPrefix(ext, ".")
		}
	}
	if item.Size > 0 {
		s := item.Size
		rec.SizeBytes = &s
	}
	if item.Sha1 != "" {
		rec.ContentHash = "sha1:" + strings.ToLower(item.Sha1)
	}
	if item.ModifiedAt != "" {
		if t, err := time.Parse(time.RFC3339, item.ModifiedAt); err == nil {
			rec.ModifiedAt = &t
		}
	}
	if item.CreatedAt != "" {
		if t, err := time.Parse(time.RFC3339, item.CreatedAt); err == nil {
			rec.CreatedAt = &t
		}
	}
	return rec
}

// buildBoxACL maps a list of collaborations onto cloud_drive grants.
// Box roles map to the cloud_drive role lattice as follows:
//
//	owner               -> owner
//	co-owner            -> owner       (close enough for read-perspective)
//	editor              -> writer
//	viewer_uploader     -> writer
//	previewer_uploader  -> writer
//	uploader            -> writer
//	previewer           -> reader
//	viewer              -> reader
//	(unknown / custom)  -> reader      (best-effort floor)
//
// Pending invitations (status != "accepted") are skipped — the
// recipient hasn't actually got access yet.
func buildBoxACL(collabs []boxCollaboration) *models.ACL {
	if len(collabs) == 0 {
		return nil
	}
	grants := make([]models.CloudDriveGrant, 0, len(collabs))
	for _, c := range collabs {
		if c.Status != "" && c.Status != "accepted" {
			continue
		}
		role := mapBoxRole(c.Role)
		if role == "" {
			continue
		}
		if c.AccessibleBy == nil {
			continue
		}
		ab := c.AccessibleBy
		principalType := "user"
		if ab.Type == "group" {
			principalType = "group"
		}
		grants = append(grants, models.CloudDriveGrant{
			Principal: models.CloudDrivePrincipal{
				Type:  principalType,
				ID:    ab.ID,
				Email: ab.Login,
				Name:  ab.Name,
			},
			Role: role,
		})
	}
	if len(grants) == 0 {
		return nil
	}
	return &models.ACL{Type: "cloud_drive", CloudDriveGrants: grants}
}

func mapBoxRole(r string) string {
	switch r {
	case "owner":
		return "owner"
	case "co-owner":
		return "owner"
	case "editor", "viewer_uploader", "previewer_uploader", "uploader":
		return "writer"
	case "viewer", "previewer":
		return "reader"
	case "":
		return ""
	}
	// Unknown role — Box may add new ones over time. Treat as
	// reader-floor rather than dropping the grant entirely; the ACL
	// chip will surface the role string verbatim if the user
	// inspects it.
	return "reader"
}
