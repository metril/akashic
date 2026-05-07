package connector

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"path"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// WebDAVConnector indexes a WebDAV-spoken endpoint. v0.11.0 / Tier 4 PR 1.
//
// Hostless source type: URL + basic auth credentials live on the
// source's connection_config. Covers the long-tail self-hosted
// installs that other connectors don't speak natively — Nextcloud,
// ownCloud, Synology File Station, generic Apache mod_dav, sabredav.
//
// Walk strategy: BFS across PROPFIND `Depth: 1` requests. Many
// servers reject `Depth: infinity` for safety (it's a denial-of-
// service vector for deep trees), so we paginate per directory
// instead of asking for the whole subtree in one shot. The shallow
// variant exposes the same per-directory listing with a single
// PROPFIND.
//
// Auth: HTTP Basic Auth. Bearer tokens (some bespoke installs) and
// digest auth (older mod_dav) are not implemented — both can be
// added later as new auth_mode values without touching the walk
// path. Client certs likewise wait on real demand.
//
// ACLs: Not surfaced. WebDAV's standard PROPFIND doesn't expose
// per-resource permissions; Nextcloud has an `oc:permissions`
// extension that returns an opaque permission string, but mapping
// that into akashic's ACL model is meaningful work beyond v0.11.0.
type WebDAVConnector struct {
	baseURL    string // e.g. https://nextcloud.example.com/remote.php/dav/files/admin/
	username   string
	password   string
	tlsVerify  bool

	httpClient *http.Client
	// Path of the parsed baseURL — kept so PROPFIND href responses
	// (which the server returns as absolute URL paths) can be
	// stripped down to the source-relative path akashic stores.
	basePath string
}

func NewWebDAVConnector(rawURL, username, password string, tlsVerify bool) *WebDAVConnector {
	return &WebDAVConnector{
		baseURL:   strings.TrimRight(rawURL, "/") + "/",
		username:  username,
		password:  password,
		tlsVerify: tlsVerify,
	}
}

func (c *WebDAVConnector) Type() string { return "webdav" }

func (c *WebDAVConnector) Connect(ctx context.Context) error {
	if c.baseURL == "" || c.baseURL == "/" {
		return fmt.Errorf("webdav: url required")
	}
	parsed, err := url.Parse(c.baseURL)
	if err != nil {
		return fmt.Errorf("webdav: parse url: %w", err)
	}
	c.basePath = parsed.Path
	if c.basePath == "" {
		c.basePath = "/"
	}

	c.httpClient = &http.Client{
		Timeout:   60 * time.Second,
		Transport: webdavTransport(c.tlsVerify),
	}

	// Smoke-test PROPFIND on the root resource. A 401 here is the
	// clearest signal that the basic auth creds are wrong; a 405
	// means the server doesn't speak WebDAV at this URL (likely a
	// plain HTTP endpoint); a 207 multistatus is the success
	// case. Anything else falls through to the connect-step error.
	if _, err := c.propfind(ctx, "", 0); err != nil {
		return fmt.Errorf("webdav: connect: %w", err)
	}
	return nil
}

// Walk paginates PROPFIND Depth: 1 requests per directory, BFS-style.
// emittedDirs guards against cycles via symlinks (some servers
// follow them); emittedDirs[path]=true the first time we list it,
// and we never list the same path twice.
func (c *WebDAVConnector) Walk(
	ctx context.Context, root string, excludePatterns []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	var stats walker.WalkStats
	if c.httpClient == nil {
		return stats, fmt.Errorf("webdav: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}

	startPath := strings.TrimPrefix(strings.TrimSuffix(root, "/"), "/")
	queue := []string{startPath}
	seen := map[string]bool{startPath: true}

	for len(queue) > 0 {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		dir := queue[0]
		queue = queue[1:]

		entries, err := c.propfind(ctx, dir, 1)
		if err != nil {
			// One unreachable subdir shouldn't tank the whole walk;
			// count it and move on. Matches the local walker's
			// permission-denied semantics.
			stats.InaccessibleDirs++
			continue
		}
		for _, e := range entries {
			if e.relPath == strings.TrimSuffix(dir, "/") {
				// PROPFIND with Depth:1 returns the parent itself
				// as the first <D:response>. Skip to avoid emitting
				// the requested directory as its own child.
				continue
			}
			base := path.Base(e.relPath)
			if excludeSet[strings.ToLower(base)] {
				continue
			}
			entry := buildWebDAVEntry(e)
			if err := fn(entry); err != nil {
				return stats, err
			}
			if e.isDir && !seen[e.relPath] {
				seen[e.relPath] = true
				queue = append(queue, e.relPath)
			}
		}
	}
	return stats, nil
}

// WalkShallow lists immediate children of `root` via a single PROPFIND
// Depth: 1 request. Files are emitted via fn; subdirectories are
// returned as basenames so the caller can fan them out as work units.
func (c *WebDAVConnector) WalkShallow(
	ctx context.Context, root string, excludePatterns []string, _ bool,
	fn func(*models.EntryRecord) error,
) ([]string, error) {
	if c.httpClient == nil {
		return nil, fmt.Errorf("webdav: not connected")
	}
	excludeSet := make(map[string]bool, len(excludePatterns))
	for _, p := range excludePatterns {
		excludeSet[strings.ToLower(p)] = true
	}
	dir := strings.TrimPrefix(strings.TrimSuffix(root, "/"), "/")
	entries, err := c.propfind(ctx, dir, 1)
	if err != nil {
		return nil, err
	}
	var subdirs []string
	for _, e := range entries {
		if e.relPath == strings.TrimSuffix(dir, "/") {
			continue
		}
		base := path.Base(e.relPath)
		if excludeSet[strings.ToLower(base)] {
			continue
		}
		if e.isDir {
			subdirs = append(subdirs, base)
			continue
		}
		entry := buildWebDAVEntry(e)
		if err := fn(entry); err != nil {
			return subdirs, err
		}
	}
	return subdirs, nil
}

func (c *WebDAVConnector) ReadFile(ctx context.Context, p string) (io.ReadCloser, error) {
	if c.httpClient == nil {
		return nil, fmt.Errorf("webdav: not connected")
	}
	target := c.baseURL + encodeWebDAVPath(strings.TrimPrefix(p, "/"))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	c.applyAuth(req)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return nil, fmt.Errorf("webdav: GET %s: %d %s", p, resp.StatusCode, snippet(body))
	}
	return resp.Body, nil
}

func (c *WebDAVConnector) Delete(ctx context.Context, p string) error {
	if c.httpClient == nil {
		return fmt.Errorf("webdav: not connected")
	}
	target := c.baseURL + encodeWebDAVPath(strings.TrimPrefix(p, "/"))
	req, err := http.NewRequestWithContext(ctx, http.MethodDelete, target, nil)
	if err != nil {
		return err
	}
	c.applyAuth(req)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("webdav: DELETE %s: %d %s", p, resp.StatusCode, snippet(body))
	}
	return nil
}

func (c *WebDAVConnector) Close() error { return nil }

// ----- PROPFIND machinery -----

// webdavEntry is the parsed-out subset of a single <D:response> node
// from a multistatus body. Surfaced back to Walk / WalkShallow so the
// caller doesn't have to learn WebDAV XML semantics.
type webdavEntry struct {
	relPath     string // path relative to the source root, no leading "/"
	isDir       bool
	size        int64
	modified    time.Time
	created     time.Time
	contentType string
	etag        string
}

// propfind issues a PROPFIND with `Depth: depth` and parses the
// returned multistatus body into per-resource entries. Auth-rejected
// (401) and method-not-allowed (405 — endpoint isn't WebDAV) bubble
// up as errors with their step inferable from the message; the
// caller's runWebDAV / Connect classifies them.
func (c *WebDAVConnector) propfind(ctx context.Context, relPath string, depth int) ([]webdavEntry, error) {
	target := c.baseURL + strings.TrimPrefix(relPath, "/")
	body := strings.NewReader(propfindBody)
	req, err := http.NewRequestWithContext(ctx, "PROPFIND", target, body)
	if err != nil {
		return nil, err
	}
	c.applyAuth(req)
	req.Header.Set("Depth", fmt.Sprintf("%d", depth))
	req.Header.Set("Content-Type", "application/xml; charset=utf-8")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	switch resp.StatusCode {
	case http.StatusUnauthorized, http.StatusForbidden:
		return nil, fmt.Errorf("auth rejected (%d): %s", resp.StatusCode, snippet(respBody))
	case http.StatusMethodNotAllowed:
		return nil, fmt.Errorf("PROPFIND not allowed (%d): server may not speak WebDAV at this URL", resp.StatusCode)
	case http.StatusNotFound:
		return nil, fmt.Errorf("resource not found (%d): %s", resp.StatusCode, target)
	case http.StatusMultiStatus, http.StatusOK:
		// fall through
	default:
		return nil, fmt.Errorf("PROPFIND %s: %d %s", target, resp.StatusCode, snippet(respBody))
	}

	var ms multistatus
	if err := xml.Unmarshal(respBody, &ms); err != nil {
		return nil, fmt.Errorf("decode multistatus: %w", err)
	}
	out := make([]webdavEntry, 0, len(ms.Responses))
	for _, r := range ms.Responses {
		entry, ok := c.parseResponse(r)
		if !ok {
			continue
		}
		out = append(out, entry)
	}
	return out, nil
}

// parseResponse converts a single <D:response> into our webdavEntry.
// Returns (_, false) when the response carries no successful
// propstat (servers sometimes return 404 props for resources that
// disappeared mid-listing).
func (c *WebDAVConnector) parseResponse(r davResponse) (webdavEntry, bool) {
	href := strings.TrimSpace(r.Href)
	if href == "" {
		return webdavEntry{}, false
	}
	// hrefs may be absolute (https://host/path) or path-only.
	// Strip the protocol/host portion if present so we can take a
	// substring against c.basePath.
	if i := strings.Index(href, "://"); i >= 0 {
		if j := strings.Index(href[i+3:], "/"); j >= 0 {
			href = href[i+3+j:]
		} else {
			href = "/"
		}
	}
	// URL-decode (server returned percent-encoded path).
	decoded, err := url.PathUnescape(href)
	if err != nil {
		decoded = href
	}
	rel := strings.TrimPrefix(decoded, c.basePath)
	rel = strings.TrimPrefix(rel, "/")
	rel = strings.TrimSuffix(rel, "/")

	// Pick the first successful propstat. Servers split props into
	// multiple propstats by status (e.g. one for 200-OK props, one
	// for 404-NotFound props on unsupported attributes).
	var prop davProp
	for _, ps := range r.PropStats {
		if strings.Contains(ps.Status, "200") {
			prop = ps.Prop
			break
		}
	}
	entry := webdavEntry{
		relPath: rel,
		isDir:   prop.ResourceType.Collection != nil,
	}
	if prop.GetContentLength != "" {
		var s int64
		fmt.Sscan(prop.GetContentLength, &s)
		entry.size = s
	}
	if prop.GetLastModified != "" {
		// RFC1123 is the WebDAV-recommended format. Some servers
		// send RFC1123Z or RFC850; tolerate both.
		for _, layout := range []string{time.RFC1123, time.RFC1123Z, time.RFC850} {
			if t, err := time.Parse(layout, prop.GetLastModified); err == nil {
				entry.modified = t
				break
			}
		}
	}
	if prop.CreationDate != "" {
		// Spec says ISO8601; some servers emit RFC1123 here too.
		for _, layout := range []string{time.RFC3339, time.RFC3339Nano, time.RFC1123, time.RFC1123Z} {
			if t, err := time.Parse(layout, prop.CreationDate); err == nil {
				entry.created = t
				break
			}
		}
	}
	entry.contentType = prop.GetContentType
	entry.etag = strings.Trim(prop.GetETag, "\"")
	return entry, true
}

func (c *WebDAVConnector) applyAuth(req *http.Request) {
	if c.username != "" || c.password != "" {
		req.SetBasicAuth(c.username, c.password)
	}
}

func buildWebDAVEntry(e webdavEntry) *models.EntryRecord {
	entry := &models.EntryRecord{
		Path: "/" + e.relPath,
		Name: path.Base(e.relPath),
	}
	if e.isDir {
		entry.Kind = "directory"
	} else {
		entry.Kind = "file"
		if e.size > 0 {
			s := e.size
			entry.SizeBytes = &s
		}
		if ext := path.Ext(entry.Name); ext != "" {
			entry.Extension = strings.TrimPrefix(ext, ".")
		}
		if e.contentType != "" {
			entry.MimeType = e.contentType
		}
		if e.etag != "" {
			entry.ContentHash = "etag:" + e.etag
		}
	}
	if !e.modified.IsZero() {
		t := e.modified
		entry.ModifiedAt = &t
	}
	if !e.created.IsZero() {
		t := e.created
		entry.CreatedAt = &t
	}
	return entry
}

// encodeWebDAVPath percent-escapes a relative WebDAV path segment-by-
// segment so characters that PROPFIND happily decoded (`#`, `?`,
// spaces, etc.) survive the round-trip back into a GET / DELETE URL.
// Forward slashes between segments are preserved verbatim — `url.PathEscape`
// would mangle them. Empty paths pass through unchanged so callers
// targeting the share root keep working.
func encodeWebDAVPath(rel string) string {
	if rel == "" {
		return ""
	}
	parts := strings.Split(rel, "/")
	for i, p := range parts {
		parts[i] = url.PathEscape(p)
	}
	return strings.Join(parts, "/")
}

func webdavTransport(tlsVerify bool) http.RoundTripper {
	t := http.DefaultTransport.(*http.Transport).Clone()
	if !tlsVerify {
		// #nosec G402 — opt-in by user; gated behind the per-source
		// `tls_verify=false` toggle for self-signed home installs.
		t.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	return t
}

// ----- XML shapes -----

const propfindBody = `<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:resourcetype/>
    <D:getcontentlength/>
    <D:getlastmodified/>
    <D:creationdate/>
    <D:getcontenttype/>
    <D:getetag/>
  </D:prop>
</D:propfind>`

type multistatus struct {
	XMLName   xml.Name      `xml:"DAV: multistatus"`
	Responses []davResponse `xml:"DAV: response"`
}

type davResponse struct {
	Href      string        `xml:"DAV: href"`
	PropStats []davPropStat `xml:"DAV: propstat"`
}

type davPropStat struct {
	Prop   davProp `xml:"DAV: prop"`
	Status string  `xml:"DAV: status"`
}

type davProp struct {
	ResourceType     davResourceType `xml:"DAV: resourcetype"`
	GetContentLength string          `xml:"DAV: getcontentlength"`
	GetLastModified  string          `xml:"DAV: getlastmodified"`
	CreationDate     string          `xml:"DAV: creationdate"`
	GetContentType   string          `xml:"DAV: getcontenttype"`
	GetETag          string          `xml:"DAV: getetag"`
}

type davResourceType struct {
	Collection *struct{} `xml:"DAV: collection"`
}

// ----- buffer helper -----

// xmlEncode is a tiny adapter so we can swap to a struct-based
// PROPFIND request body later (e.g., to ask for `oc:permissions` for
// Nextcloud) without rewriting propfind(). Currently a no-op; the
// constant body above is sufficient for the standard prop set.
var _ = bytes.NewBuffer
