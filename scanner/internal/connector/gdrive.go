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

// GDriveConfig is the connection_config shape the API hands the scanner
// for a Google Drive source. ``access_token`` is minted at scan-lease
// time by ``api.routers.scanners.lease_scan`` from the source's
// SourceOAuthCredential row; for long scans the scanner re-mints via
// POST /api/scanners/oauth/access-token.
//
// FolderID is optional — when empty, the connector walks "My Drive"
// (the API uses the special "root" alias). When set, the walk is
// scoped to that folder and its subtree (useful for users with
// massive drives who only want one project indexed).
type GDriveConfig struct {
	AccessToken string
	FolderID    string
}

// GDriveConnector walks a Google Drive subtree via the v3 REST API.
// The connector talks plain HTTP + JSON rather than pulling in
// google.golang.org/api/drive/v3; the surface we use is small enough
// that the SDK's value (auto-pagination, retries) doesn't justify the
// 100+ transitive deps it brings in.
//
// Path synthesis: Drive addresses files by opaque ID and lets two
// siblings share a name. The walker emits a synthesized display path
// (``/My Drive/Foo/Bar.docx``) and persists the opaque ID in
// EntryRecord.NativeID. On collision we append `` (id)`` to the name
// segment so the path stays unique within akashic's
// (source_id, path) uniqueness constraint.
//
// Permissions → cloud_drive ACL: each file's ``permissions`` array is
// mapped principal-by-principal; ``inherited`` flags grants Drive
// reports as inherited.
type GDriveConnector struct {
	cfg        *GDriveConfig
	httpClient *http.Client

	// Refresh callback. The agent installs a closure that POSTs
	// /api/scanners/oauth/access-token; nil in tests.
	refreshAccessToken func(ctx context.Context) (string, error)

	// Atomic so the refresh closure can update without locking.
	currentToken atomic.Value // string
}

// NewGDriveConnector wires the connector with a static access token.
// The agent layer can install a refresh callback after construction
// via SetRefreshCallback for scans that outlast the access-token TTL.
func NewGDriveConnector(cfg *GDriveConfig) *GDriveConnector {
	c := &GDriveConnector{
		cfg:        cfg,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
	c.currentToken.Store(cfg.AccessToken)
	return c
}

// SetRefreshCallback installs the function that mints a fresh access
// token mid-scan. Called when a request returns 401, after which the
// caller retries the request once with the refreshed token.
func (c *GDriveConnector) SetRefreshCallback(fn func(ctx context.Context) (string, error)) {
	c.refreshAccessToken = fn
}

func (c *GDriveConnector) Type() string { return "gdrive" }

// Connect verifies the access token works against the Drive API and
// surfaces the connected user's email for diagnostics. about.get is
// cheap and exercises the same auth path the walk will use.
func (c *GDriveConnector) Connect(ctx context.Context) error {
	if c.cfg == nil || c.cfg.AccessToken == "" {
		return errors.New("gdrive: missing access_token in connection_config (no OAuth credential connected to source)")
	}
	body, err := c.do(ctx, "GET",
		"https://www.googleapis.com/drive/v3/about?fields=user/emailAddress",
		nil)
	if err != nil {
		return fmt.Errorf("gdrive: about.get failed: %w", err)
	}
	defer body.Close()
	var about struct {
		User struct {
			EmailAddress string `json:"emailAddress"`
		} `json:"user"`
	}
	if err := json.NewDecoder(body).Decode(&about); err != nil {
		return fmt.Errorf("gdrive: about.get decode: %w", err)
	}
	return nil
}

func (c *GDriveConnector) Close() error { return nil }

// Walk performs a BFS over the Drive subtree rooted at ``root`` (the
// argument here is akashic's display path, e.g. ``/My Drive``; we
// translate to a Drive folder id via the configured FolderID, defaulting
// to ``root`` for "My Drive").
//
// Excludes are matched against the synthesized display path using
// the standard walker.MatchAny rules.
//
// Hash: Drive returns md5Checksum directly on binary files (Google-
// format docs leave it empty). When present, emit ``md5:<hex>`` so
// content_hash plays nicely with the existing prefix-tagged hash
// vocabulary.
func (c *GDriveConnector) Walk(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fullScan bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	stats := walker.WalkStats{}

	rootID := c.cfg.FolderID
	if rootID == "" {
		rootID = "root"
	}
	rootName := "My Drive"
	if c.cfg.FolderID != "" {
		// Resolve the folder name so the synthesized path leads with
		// something meaningful rather than the opaque id.
		name, err := c.fetchFolderName(ctx, c.cfg.FolderID)
		if err != nil {
			return stats, fmt.Errorf("gdrive: resolve folder %q: %w", c.cfg.FolderID, err)
		}
		rootName = name
	}
	rootPath := "/" + rootName

	// Emit the root folder itself.
	if err := fn(&models.EntryRecord{
		Path:     rootPath,
		Name:     rootName,
		Kind:     "directory",
		NativeID: rootID,
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

		seenNames := map[string]int{}
		err := c.listChildren(ctx, cur.id, func(child driveFile) error {
			displayName := child.Name
			// Drive lets siblings share a name. Disambiguate by
			// appending the id to subsequent collisions; the first
			// occurrence keeps the bare name.
			if existed := seenNames[child.Name]; existed > 0 {
				displayName = fmt.Sprintf("%s (%s)", child.Name, child.ID)
			}
			seenNames[child.Name]++

			childPath := path.Join(cur.path, displayName)
			if matchExcludes(excludePatterns, childPath) {
				return nil
			}
			rec := buildGDriveEntry(child, childPath, displayName)
			if !computeHash {
				// Existing semantics: when the agent hasn't asked for
				// content hashes (the "structure-only" full scan)
				// drop the md5 we already have so we don't surface a
				// hash that doesn't match what was promised.
				rec.ContentHash = ""
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
			// One failed listing under a deep folder shouldn't kill
			// the entire walk — bump the inaccessible counter and
			// continue. Connect-level failures (auth) are rejected at
			// Connect; per-folder failures here are usually quota or
			// transient 5xx.
			stats.InaccessibleDirs++
		}
	}
	return stats, nil
}

// WalkShallow lists the immediate children of root, returning the
// subdirectory ids so the unit-based agent can fan them out across
// scanners.
func (c *GDriveConnector) WalkShallow(
	ctx context.Context,
	root string,
	excludePatterns []string,
	computeHash bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	rootID := c.cfg.FolderID
	if rootID == "" {
		rootID = "root"
	}
	rootName := "My Drive"
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
		NativeID: rootID,
	}); err != nil {
		return nil, err
	}

	subdirs := []string{}
	seenNames := map[string]int{}
	err := c.listChildren(ctx, rootID, func(child driveFile) error {
		displayName := child.Name
		if existed := seenNames[child.Name]; existed > 0 {
			displayName = fmt.Sprintf("%s (%s)", child.Name, child.ID)
		}
		seenNames[child.Name]++
		childPath := path.Join(rootPath, displayName)
		if matchExcludes(excludePatterns, childPath) {
			return nil
		}
		rec := buildGDriveEntry(child, childPath, displayName)
		if !computeHash {
			rec.ContentHash = ""
		}
		if rec.IsDir() {
			subdirs = append(subdirs, childPath)
			return nil // The unit-coordinated agent walks the subtree separately.
		}
		return fn(rec)
	})
	return subdirs, err
}

// ReadFile downloads a Drive file's binary content. Google-format docs
// (Docs/Sheets/Slides) are not directly downloadable — the caller must
// decide on an export format; for v0.14.0 we error out for them rather
// than silently exporting a default.
//
// `path` is the synthesized display path; we round-trip to the Drive
// id by re-traversing — a future optimisation would cache the
// (path → id) map. Most ReadFile callers (Tika extraction, content-fetch
// preview) operate on the same handful of files they just got from a
// Walk callback so the duplication is small.
func (c *GDriveConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	id, mimeType, err := c.resolveFileIDByPath(ctx, p)
	if err != nil {
		return nil, err
	}
	if strings.HasPrefix(mimeType, "application/vnd.google-apps") {
		return nil, fmt.Errorf(
			"gdrive: %s is a Google-format doc (mime %s); export-format negotiation not yet implemented",
			p, mimeType,
		)
	}
	body, err := c.do(ctx, "GET",
		"https://www.googleapis.com/drive/v3/files/"+url.PathEscape(id)+"?alt=media",
		nil)
	if err != nil {
		return nil, err
	}
	return body, nil
}

// Delete is a no-op on Drive — duplicate-delete on cloud-drive sources
// isn't supported. Returning an explicit error is friendlier than a
// silent success.
func (c *GDriveConnector) Delete(ctx context.Context, p string) error {
	return errors.New("gdrive: delete not supported (use Google Drive directly)")
}

// ── Internal Drive REST helpers ───────────────────────────────────────────

// driveFile is the subset of a Drive v3 File resource we actually use.
type driveFile struct {
	ID           string             `json:"id"`
	Name         string             `json:"name"`
	MimeType     string             `json:"mimeType"`
	Size         string             `json:"size"`
	ModifiedTime string             `json:"modifiedTime"`
	CreatedTime  string             `json:"createdTime"`
	Md5Checksum  string             `json:"md5Checksum"`
	Trashed      bool               `json:"trashed"`
	Shared       bool               `json:"shared"`
	Parents      []string           `json:"parents"`
	Owners       []drivePermission  `json:"owners"`
	Permissions  []drivePermission  `json:"permissions"`
}

type drivePermission struct {
	ID                   string `json:"id"`
	Type                 string `json:"type"`
	Role                 string `json:"role"`
	EmailAddress         string `json:"emailAddress"`
	DisplayName          string `json:"displayName"`
	Domain               string `json:"domain"`
	InheritedPermission  bool   `json:"inherited"`
	InheritedFromID      string `json:"inheritedFrom"`
}

// driveListPage is one page of a files.list response.
type driveListPage struct {
	NextPageToken string      `json:"nextPageToken"`
	Files         []driveFile `json:"files"`
}

// listChildren paginates through files.list with parent==parentID and
// invokes ``cb`` for each child.
const driveListFields = "nextPageToken,files(id,name,mimeType,size,modifiedTime,createdTime,md5Checksum,trashed,shared,parents,owners(id,emailAddress,displayName,domain),permissions(id,type,role,emailAddress,displayName,domain,inherited,inheritedFrom))"

func (c *GDriveConnector) listChildren(
	ctx context.Context,
	parentID string,
	cb func(driveFile) error,
) error {
	pageToken := ""
	for {
		q := url.Values{}
		q.Set("q", fmt.Sprintf("'%s' in parents and trashed = false", parentID))
		q.Set("pageSize", "100")
		q.Set("fields", driveListFields)
		// Order by name so name-collision disambiguation is stable
		// across runs (the first sibling alphabetically keeps the
		// bare name; later ones get the (id) suffix).
		q.Set("orderBy", "name")
		if pageToken != "" {
			q.Set("pageToken", pageToken)
		}
		body, err := c.do(ctx, "GET",
			"https://www.googleapis.com/drive/v3/files?"+q.Encode(),
			nil)
		if err != nil {
			return err
		}
		var page driveListPage
		err = json.NewDecoder(body).Decode(&page)
		body.Close()
		if err != nil {
			return fmt.Errorf("gdrive: files.list decode: %w", err)
		}
		for i := range page.Files {
			if err := cb(page.Files[i]); err != nil {
				return err
			}
		}
		if page.NextPageToken == "" {
			return nil
		}
		pageToken = page.NextPageToken
	}
}

func (c *GDriveConnector) fetchFolderName(ctx context.Context, id string) (string, error) {
	body, err := c.do(ctx, "GET",
		"https://www.googleapis.com/drive/v3/files/"+url.PathEscape(id)+"?fields=name",
		nil)
	if err != nil {
		return "", err
	}
	defer body.Close()
	var f driveFile
	if err := json.NewDecoder(body).Decode(&f); err != nil {
		return "", err
	}
	return f.Name, nil
}

func (c *GDriveConnector) resolveFileIDByPath(
	ctx context.Context, p string,
) (id string, mimeType string, err error) {
	// Walk the path segment-by-segment. The first segment is the Drive
	// root name (My Drive or the configured FolderID's name); subsequent
	// segments resolve via files.list under the previous segment's id.
	parts := splitDrivePath(p)
	if len(parts) == 0 {
		return "", "", fmt.Errorf("gdrive: empty path")
	}
	rootID := c.cfg.FolderID
	if rootID == "" {
		rootID = "root"
	}
	parentID := rootID
	parentMime := "application/vnd.google-apps.folder"
	for _, seg := range parts[1:] { // skip root segment
		// Strip any " (id)" suffix our path synthesis added.
		bareName, hintID := stripCollisionHint(seg)
		var matched *driveFile
		err := c.listChildren(ctx, parentID, func(child driveFile) error {
			if matched != nil {
				return nil
			}
			if hintID != "" && child.ID == hintID {
				ch := child
				matched = &ch
			} else if hintID == "" && child.Name == bareName {
				ch := child
				matched = &ch
			}
			return nil
		})
		if err != nil {
			return "", "", err
		}
		if matched == nil {
			return "", "", fmt.Errorf("gdrive: path segment %q not found under %q", seg, parentID)
		}
		parentID = matched.ID
		parentMime = matched.MimeType
	}
	return parentID, parentMime, nil
}

// matchExcludes returns true when ``p`` (or any of its path segments)
// matches one of the exclude patterns case-insensitively. Mirrors the
// simple substring-or-segment match the WebDAV connector uses; full
// glob support lives in the local walker.
func matchExcludes(patterns []string, p string) bool {
	if len(patterns) == 0 {
		return false
	}
	pl := strings.ToLower(p)
	for _, pat := range patterns {
		if pat == "" {
			continue
		}
		if strings.Contains(pl, strings.ToLower(pat)) {
			return true
		}
	}
	return false
}

func splitDrivePath(p string) []string {
	clean := strings.Trim(p, "/")
	if clean == "" {
		return nil
	}
	return strings.Split(clean, "/")
}

func stripCollisionHint(seg string) (name, id string) {
	// Disambiguation suffix shape: "Foo (drive-id-123)". We only strip
	// when the suffix looks plausibly like a Drive id (no spaces, mixed
	// case alphanumerics + dashes/underscores) so we don't mangle real
	// names that happen to contain parens.
	open := strings.LastIndex(seg, " (")
	if open < 0 || !strings.HasSuffix(seg, ")") {
		return seg, ""
	}
	idPart := seg[open+2 : len(seg)-1]
	if idPart == "" {
		return seg, ""
	}
	for _, ch := range idPart {
		if !(ch == '-' || ch == '_' || (ch >= '0' && ch <= '9') ||
			(ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')) {
			return seg, ""
		}
	}
	return seg[:open], idPart
}

// do issues an authenticated request with one 401-driven refresh+retry.
func (c *GDriveConnector) do(ctx context.Context, method, url string, body io.Reader) (io.ReadCloser, error) {
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
		// Drain + close the 401 body before retrying.
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		fresh, refreshErr := c.refreshAccessToken(ctx)
		if refreshErr != nil {
			return nil, fmt.Errorf("gdrive: 401 and refresh failed: %w", refreshErr)
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
			"gdrive: %s %s -> %d: %s",
			method, url, resp.StatusCode,
			strings.TrimSpace(string(buf)),
		)
	}
	return resp.Body, nil
}

// ── Wire-shape mappers ────────────────────────────────────────────────────

const driveFolderMime = "application/vnd.google-apps.folder"

func buildGDriveEntry(f driveFile, displayPath, displayName string) *models.EntryRecord {
	rec := &models.EntryRecord{
		Path:     displayPath,
		Name:     displayName,
		NativeID: f.ID,
		MimeType: f.MimeType,
	}
	if f.MimeType == driveFolderMime {
		rec.Kind = "directory"
	} else {
		rec.Kind = "file"
		ext := path.Ext(f.Name)
		if ext != "" {
			rec.Extension = strings.TrimPrefix(ext, ".")
		}
	}
	if f.Size != "" {
		// Drive sends size as a string; Google-format docs omit it.
		var n int64
		_, _ = fmt.Sscanf(f.Size, "%d", &n)
		rec.SizeBytes = &n
	}
	if f.Md5Checksum != "" {
		rec.ContentHash = "md5:" + f.Md5Checksum
	}
	if f.ModifiedTime != "" {
		if t, err := time.Parse(time.RFC3339, f.ModifiedTime); err == nil {
			rec.ModifiedAt = &t
		}
	}
	if f.CreatedTime != "" {
		if t, err := time.Parse(time.RFC3339, f.CreatedTime); err == nil {
			rec.CreatedAt = &t
		}
	}
	if len(f.Owners) > 0 {
		owner := f.Owners[0]
		rec.OwnerName = owner.DisplayName
		if rec.OwnerName == "" {
			rec.OwnerName = owner.EmailAddress
		}
	}
	rec.Acl = buildGDriveACL(f)
	return rec
}

// buildGDriveACL maps a file's Drive permissions into the cloud_drive
// ACL discriminator. Drive's roles map cleanly:
//
//	owner            -> owner
//	organizer        -> owner       (Shared Drive top-level admin)
//	fileOrganizer    -> file_organizer
//	writer           -> writer
//	commenter        -> commenter
//	reader           -> reader
//
// We pass the DisplayName + EmailAddress through so the API doesn't
// have to round-trip to Drive again at render time.
func buildGDriveACL(f driveFile) *models.ACL {
	if len(f.Permissions) == 0 && len(f.Owners) == 0 {
		return nil
	}
	grants := make([]models.CloudDriveGrant, 0, len(f.Permissions)+len(f.Owners))
	for _, perm := range f.Permissions {
		role := mapDriveRole(perm.Role)
		if role == "" {
			continue
		}
		principalType := perm.Type
		if principalType == "" {
			principalType = "user"
		}
		id := perm.ID
		if principalType == "domain" && id == "" {
			id = perm.Domain
		}
		if principalType == "anyone" && id == "" {
			id = "anyone"
		}
		grants = append(grants, models.CloudDriveGrant{
			Principal: models.CloudDrivePrincipal{
				Type:  principalType,
				ID:    id,
				Email: perm.EmailAddress,
				Name:  perm.DisplayName,
			},
			Role:            role,
			Inherited:       perm.InheritedPermission,
			InheritedFromID: perm.InheritedFromID,
		})
	}
	return &models.ACL{
		Type:             "cloud_drive",
		CloudDriveGrants: grants,
	}
}

func mapDriveRole(r string) string {
	switch r {
	case "owner":
		return "owner"
	case "organizer":
		return "owner" // shared-drive admin
	case "fileOrganizer":
		return "file_organizer"
	case "writer":
		return "writer"
	case "commenter":
		return "commenter"
	case "reader":
		return "reader"
	}
	return ""
}
