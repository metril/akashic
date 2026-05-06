package connector

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"path"
	"strings"
	"sync/atomic"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// DropboxConfig is the connection_config shape for a Dropbox source.
//
// Path is optional. Empty string == "scan from the root of the user's
// Dropbox" (which is the literal Dropbox API requirement: empty
// string, not ``"/"`` — that's the one path call that's NOT slash-
// rooted). Non-empty values must start with ``/`` and identify a
// folder under the root.
type DropboxConfig struct {
	AccessToken string
	Path        string
}

// DropboxConnector indexes a Dropbox account via the public REST API.
//
// Differences from the Graph/Drive connectors:
//
//  - All endpoints are POST with a JSON body — even read-only listings.
//  - The walk is a single ``list_folder`` call with ``recursive=true``
//    plus ``list_folder/continue`` pagination, rather than per-folder
//    BFS. Substantially fewer round-trips on big drives.
//  - Path is the canonical addressing primitive — no opaque-id juggling
//    like Drive. ``path_display`` is what the user sees and what the
//    walker emits as the entry path.
//  - ``content_hash`` is Dropbox's block-based SHA-256, prefixed
//    ``dropbox:`` so it doesn't collide with the regular ``sha256:``
//    hashes from OneDrive.
//
// Sharing → cloud_drive ACL is NOT wired for v0.17.0 — surfacing the
// per-file/per-folder member list takes one extra API call per
// shared item, which is too expensive on the common case (most
// items unshared). A follow-up release adds best-effort enrichment.
type DropboxConnector struct {
	cfg                *DropboxConfig
	httpClient         *http.Client
	refreshAccessToken func(ctx context.Context) (string, error)
	currentToken       atomic.Value // string
}

func NewDropboxConnector(cfg *DropboxConfig) *DropboxConnector {
	c := &DropboxConnector{
		cfg:        cfg,
		httpClient: &http.Client{Timeout: 60 * time.Second},
	}
	c.currentToken.Store(cfg.AccessToken)
	return c
}

func (c *DropboxConnector) SetRefreshCallback(fn func(ctx context.Context) (string, error)) {
	c.refreshAccessToken = fn
}

func (c *DropboxConnector) Type() string { return "dropbox" }

func (c *DropboxConnector) Connect(ctx context.Context) error {
	if c.cfg == nil || c.cfg.AccessToken == "" {
		return errors.New("dropbox: missing access_token in connection_config (no OAuth credential connected to source)")
	}
	body, err := c.do(ctx,
		"https://api.dropboxapi.com/2/users/get_current_account",
		nil) // null body — Dropbox accepts a literal "null" or empty
	if err != nil {
		return fmt.Errorf("dropbox: get_current_account failed: %w", err)
	}
	defer body.Close()
	var account struct {
		Email   string `json:"email"`
		Name    struct {
			DisplayName string `json:"display_name"`
		} `json:"name"`
	}
	if err := json.NewDecoder(body).Decode(&account); err != nil {
		return fmt.Errorf("dropbox: account decode: %w", err)
	}
	return nil
}

func (c *DropboxConnector) Close() error { return nil }

func (c *DropboxConnector) Walk(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fullScan bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	stats := walker.WalkStats{}

	// Dropbox's "scan from root" path is the literal empty string.
	// User-supplied paths start with "/"; pass them through verbatim.
	rootPath := strings.TrimSpace(c.cfg.Path)
	if rootPath == "/" {
		rootPath = ""
	}

	body, err := c.do(ctx,
		"https://api.dropboxapi.com/2/files/list_folder",
		map[string]any{
			"path":                                 rootPath,
			"recursive":                            true,
			"include_deleted":                      false,
			"include_has_explicit_shared_members":  true,
			"include_mounted_folders":              true,
			"limit":                                2000,
		})
	if err != nil {
		return stats, err
	}
	page, err := decodeListFolderPage(body)
	body.Close()
	if err != nil {
		return stats, err
	}

	emit := func(entries []dropboxEntry) error {
		for _, e := range entries {
			rec := buildDropboxEntry(e, computeHash)
			if rec == nil {
				continue
			}
			if matchExcludes(excludePatterns, rec.Path) {
				continue
			}
			c.enrichDropboxACL(ctx, e, rec)
			if err := fn(rec); err != nil {
				return err
			}
		}
		return nil
	}

	if err := emit(page.Entries); err != nil {
		return stats, err
	}

	cursor := page.Cursor
	for page.HasMore {
		nextBody, err := c.do(ctx,
			"https://api.dropboxapi.com/2/files/list_folder/continue",
			map[string]any{"cursor": cursor})
		if err != nil {
			return stats, err
		}
		page, err = decodeListFolderPage(nextBody)
		nextBody.Close()
		if err != nil {
			return stats, err
		}
		if err := emit(page.Entries); err != nil {
			return stats, err
		}
		cursor = page.Cursor
	}
	return stats, nil
}

// WalkShallow lists the immediate children of root (recursive=false).
// Used by the unit-coordinated agent for top-level enumeration.
func (c *DropboxConnector) WalkShallow(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	rootPath := strings.TrimSpace(c.cfg.Path)
	if rootPath == "/" {
		rootPath = ""
	}
	body, err := c.do(ctx,
		"https://api.dropboxapi.com/2/files/list_folder",
		map[string]any{
			"path":                                rootPath,
			"recursive":                           false,
			"include_deleted":                     false,
			"include_has_explicit_shared_members": true,
			"limit":                               2000,
		})
	if err != nil {
		return nil, err
	}
	page, err := decodeListFolderPage(body)
	body.Close()
	if err != nil {
		return nil, err
	}
	subdirs := []string{}
	cursor := page.Cursor
	for {
		for _, e := range page.Entries {
			rec := buildDropboxEntry(e, computeHash)
			if rec == nil {
				continue
			}
			if matchExcludes(excludePatterns, rec.Path) {
				continue
			}
			if rec.IsDir() {
				subdirs = append(subdirs, rec.Path)
				continue
			}
			c.enrichDropboxACL(ctx, e, rec)
			if err := fn(rec); err != nil {
				return subdirs, err
			}
		}
		if !page.HasMore {
			break
		}
		nb, err := c.do(ctx,
			"https://api.dropboxapi.com/2/files/list_folder/continue",
			map[string]any{"cursor": cursor})
		if err != nil {
			return subdirs, err
		}
		page, err = decodeListFolderPage(nb)
		nb.Close()
		if err != nil {
			return subdirs, err
		}
		cursor = page.Cursor
	}
	return subdirs, nil
}

// ReadFile downloads a Dropbox file's content. The download endpoint
// lives on a different host (``content.dropboxapi.com``) and takes
// the path via the ``Dropbox-API-Arg`` header rather than a body.
func (c *DropboxConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	apiArg, err := json.Marshal(map[string]string{"path": p})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, "POST",
		"https://content.dropboxapi.com/2/files/download", nil)
	if err != nil {
		return nil, err
	}
	tok, _ := c.currentToken.Load().(string)
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Dropbox-API-Arg", string(apiArg))
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized && c.refreshAccessToken != nil {
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		fresh, refreshErr := c.refreshAccessToken(ctx)
		if refreshErr != nil {
			return nil, fmt.Errorf("dropbox: 401 and refresh failed: %w", refreshErr)
		}
		c.currentToken.Store(fresh)
		req2, err := http.NewRequestWithContext(ctx, "POST",
			"https://content.dropboxapi.com/2/files/download", nil)
		if err != nil {
			return nil, err
		}
		req2.Header.Set("Authorization", "Bearer "+fresh)
		req2.Header.Set("Dropbox-API-Arg", string(apiArg))
		resp, err = c.httpClient.Do(req2)
		if err != nil {
			return nil, err
		}
	}
	if resp.StatusCode >= 400 {
		buf, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, fmt.Errorf("dropbox: download %s -> %d: %s",
			p, resp.StatusCode, strings.TrimSpace(string(buf)))
	}
	return resp.Body, nil
}

// Delete is a no-op on Dropbox — duplicate-delete on cloud-drive
// sources isn't supported; deleting from akashic shouldn't reach
// out and remove the source-of-truth file in someone's Dropbox.
func (c *DropboxConnector) Delete(ctx context.Context, p string) error {
	return errors.New("dropbox: delete not supported (use Dropbox directly)")
}

// ── Internal helpers ─────────────────────────────────────────────────────

type dropboxEntry struct {
	Tag                          string `json:".tag"` // file | folder | deleted
	Name                         string `json:"name"`
	PathLower                    string `json:"path_lower"`
	PathDisplay                  string `json:"path_display"`
	ID                           string `json:"id"`
	ClientModified               string `json:"client_modified"`
	ServerModified               string `json:"server_modified"`
	Size                         int64  `json:"size"`
	ContentHash                  string `json:"content_hash"`
	Rev                          string `json:"rev"`
	HasExplicitSharedMembers     bool   `json:"has_explicit_shared_members"`
	SharingInfo                  *struct {
		ParentSharedFolderID string `json:"parent_shared_folder_id"`
		SharedFolderID       string `json:"shared_folder_id"`
		ReadOnly             bool   `json:"read_only"`
	} `json:"sharing_info,omitempty"`
}

type dropboxListPage struct {
	Entries []dropboxEntry `json:"entries"`
	Cursor  string         `json:"cursor"`
	HasMore bool           `json:"has_more"`
}

// dropboxMember is one user / group entry returned by sharing/list_*
// _members. Both the user-shaped and group-shaped responses fit; the
// fields that aren't applicable stay zero. Pending invitees come back
// in a separate ``invitees`` slice we don't surface — they don't have
// access yet.
type dropboxMember struct {
	User *struct {
		AccountID   string `json:"account_id"`
		Email       string `json:"email"`
		DisplayName string `json:"display_name"`
	} `json:"user,omitempty"`
	Group *struct {
		GroupID   string `json:"group_id"`
		GroupName string `json:"group_name"`
	} `json:"group,omitempty"`
	AccessType struct {
		Tag string `json:".tag"` // owner | editor | viewer | viewer_no_comment
	} `json:"access_type"`
	IsInherited bool `json:"is_inherited"`
}

type dropboxMembersResponse struct {
	Users  []dropboxMember `json:"users"`
	Groups []dropboxMember `json:"groups"`
	Cursor string          `json:"cursor"`
}

func decodeListFolderPage(body io.Reader) (dropboxListPage, error) {
	var page dropboxListPage
	if err := json.NewDecoder(body).Decode(&page); err != nil {
		return page, fmt.Errorf("dropbox: list_folder decode: %w", err)
	}
	return page, nil
}

// do issues an authenticated POST with a JSON body. Dropbox uses
// ``application/json`` for everything; even calls with no parameters
// expect a literal ``null`` body. ``body=nil`` here sends ``null`` to
// keep the API happy.
func (c *DropboxConnector) do(ctx context.Context, url string, body any) (io.ReadCloser, error) {
	var payload []byte
	if body != nil {
		var err error
		payload, err = json.Marshal(body)
		if err != nil {
			return nil, err
		}
	} else {
		payload = []byte("null")
	}
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	tok, _ := c.currentToken.Load().(string)
	req.Header.Set("Authorization", "Bearer "+tok)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized && c.refreshAccessToken != nil {
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		fresh, refreshErr := c.refreshAccessToken(ctx)
		if refreshErr != nil {
			return nil, fmt.Errorf("dropbox: 401 and refresh failed: %w", refreshErr)
		}
		c.currentToken.Store(fresh)
		req2, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(payload))
		if err != nil {
			return nil, err
		}
		req2.Header.Set("Authorization", "Bearer "+fresh)
		req2.Header.Set("Content-Type", "application/json")
		resp, err = c.httpClient.Do(req2)
		if err != nil {
			return nil, err
		}
	}
	if resp.StatusCode >= 400 {
		buf, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, fmt.Errorf("dropbox: POST %s -> %d: %s",
			url, resp.StatusCode, strings.TrimSpace(string(buf)))
	}
	return resp.Body, nil
}

// fetchFolderMembers / fetchFileMembers pull the explicit-share list
// for an item that ``sharing_info`` flagged as shared. Pagination is
// supported via ``list_*_members/continue`` in the Dropbox API but
// in practice the default first-page limit (100) is plenty — items
// shared with > 100 named principals are exceedingly rare and
// truncating there is acceptable for v0.18.1. A future pass can
// follow the cursor.
func (c *DropboxConnector) fetchFolderMembers(
	ctx context.Context, sharedFolderID string,
) (*dropboxMembersResponse, error) {
	body, err := c.do(ctx,
		"https://api.dropboxapi.com/2/sharing/list_folder_members",
		map[string]any{"shared_folder_id": sharedFolderID})
	if err != nil {
		return nil, err
	}
	defer body.Close()
	var resp dropboxMembersResponse
	if err := json.NewDecoder(body).Decode(&resp); err != nil {
		return nil, fmt.Errorf("dropbox: list_folder_members decode: %w", err)
	}
	return &resp, nil
}

func (c *DropboxConnector) fetchFileMembers(
	ctx context.Context, fileID string,
) (*dropboxMembersResponse, error) {
	body, err := c.do(ctx,
		"https://api.dropboxapi.com/2/sharing/list_file_members",
		map[string]any{
			"file":                  fileID,
			"include_inherited":     true,
			"limit":                 100,
		})
	if err != nil {
		return nil, err
	}
	defer body.Close()
	var resp dropboxMembersResponse
	if err := json.NewDecoder(body).Decode(&resp); err != nil {
		return nil, fmt.Errorf("dropbox: list_file_members decode: %w", err)
	}
	return &resp, nil
}

// buildDropboxACL converts Dropbox's user + group member lists into
// the cloud_drive ACL shape. Dropbox access types map cleanly:
//
//	owner             -> owner
//	editor            -> writer
//	viewer            -> reader
//	viewer_no_comment -> reader
//
// Inherited grants survive the round-trip via ``IsInherited``.
func buildDropboxACL(resp *dropboxMembersResponse) *models.ACL {
	if resp == nil {
		return nil
	}
	grants := make([]models.CloudDriveGrant, 0, len(resp.Users)+len(resp.Groups))
	for _, m := range resp.Users {
		role := mapDropboxAccessType(m.AccessType.Tag)
		if role == "" || m.User == nil {
			continue
		}
		grants = append(grants, models.CloudDriveGrant{
			Principal: models.CloudDrivePrincipal{
				Type:  "user",
				ID:    m.User.AccountID,
				Email: m.User.Email,
				Name:  m.User.DisplayName,
			},
			Role:      role,
			Inherited: m.IsInherited,
		})
	}
	for _, m := range resp.Groups {
		role := mapDropboxAccessType(m.AccessType.Tag)
		if role == "" || m.Group == nil {
			continue
		}
		grants = append(grants, models.CloudDriveGrant{
			Principal: models.CloudDrivePrincipal{
				Type: "group",
				ID:   m.Group.GroupID,
				Name: m.Group.GroupName,
			},
			Role:      role,
			Inherited: m.IsInherited,
		})
	}
	if len(grants) == 0 {
		return nil
	}
	return &models.ACL{Type: "cloud_drive", CloudDriveGrants: grants}
}

func mapDropboxAccessType(tag string) string {
	switch tag {
	case "owner":
		return "owner"
	case "editor":
		return "writer"
	case "viewer", "viewer_no_comment":
		return "reader"
	}
	return ""
}

// enrichDropboxACL fetches the per-item member list when sharing_info
// indicates the item is explicitly shared, and attaches the resulting
// cloud_drive ACL onto rec. Best-effort: API failures are swallowed
// (we'd rather keep the entry with no ACL than fail the whole walk
// over a transient sharing API hiccup).
func (c *DropboxConnector) enrichDropboxACL(
	ctx context.Context, e dropboxEntry, rec *models.EntryRecord,
) {
	if rec == nil || e.SharingInfo == nil {
		return
	}
	switch e.Tag {
	case "folder":
		// shared_folder_id presence on a folder means the folder
		// itself is the share root.
		if e.SharingInfo.SharedFolderID == "" {
			return
		}
		members, err := c.fetchFolderMembers(ctx, e.SharingInfo.SharedFolderID)
		if err != nil {
			return
		}
		rec.Acl = buildDropboxACL(members)
	case "file":
		// has_explicit_shared_members marks files that have been
		// shared independently of any parent shared folder.
		if !e.HasExplicitSharedMembers {
			return
		}
		members, err := c.fetchFileMembers(ctx, e.ID)
		if err != nil {
			return
		}
		rec.Acl = buildDropboxACL(members)
	}
}

// buildDropboxEntry converts a Dropbox API entry into the akashic
// EntryRecord shape. Returns nil for ".deleted" entries (we don't
// emit those — the api's deletion-detection layer notices missing
// entries on the next scan).
func buildDropboxEntry(e dropboxEntry, computeHash bool) *models.EntryRecord {
	if e.Tag == "deleted" {
		return nil
	}
	display := e.PathDisplay
	if display == "" {
		display = e.PathLower
	}
	if display == "" || e.Name == "" {
		return nil
	}
	rec := &models.EntryRecord{
		Path:     display,
		Name:     e.Name,
		NativeID: e.ID,
	}
	switch e.Tag {
	case "folder":
		rec.Kind = "directory"
	case "file":
		rec.Kind = "file"
		ext := path.Ext(e.Name)
		if ext != "" {
			rec.Extension = strings.TrimPrefix(ext, ".")
		}
	default:
		// Unknown tag — skip rather than emit a malformed entry.
		return nil
	}
	if e.Tag == "file" && e.Size > 0 {
		s := e.Size
		rec.SizeBytes = &s
	}
	// Dropbox content_hash is a block-based SHA-256 — prefix it so
	// dedup queries don't accidentally collide it with a normal
	// SHA-256 from another source type.
	if computeHash && e.ContentHash != "" {
		rec.ContentHash = "dropbox:" + e.ContentHash
	}
	if e.ServerModified != "" {
		if t, err := time.Parse(time.RFC3339, e.ServerModified); err == nil {
			rec.ModifiedAt = &t
		}
	}
	if e.ClientModified != "" {
		if t, err := time.Parse(time.RFC3339, e.ClientModified); err == nil {
			rec.CreatedAt = &t // Dropbox doesn't expose true creation time;
			// client_modified is the closest analog (when the client
			// uploaded the file). Better than nothing.
		}
	}
	return rec
}
