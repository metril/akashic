package connector

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// PaperlessConnector indexes a Paperless-ngx instance via its REST API.
//
// Tier 3 / v0.7.0. Source-only (no Host row): the URL + token live on
// the source's connection_config. There's exactly one logical "share"
// per Paperless instance (the user's whole document library); the
// optional `tag_filter` narrows the scan to a tag whitelist if set.
//
// The scanner walks `GET /api/documents/?page=N` and emits one
// EntryRecord per document, plus directory entries for the synthetic
// `/<correspondent>/<document_type>/<year>/` hierarchy. Document
// metadata (correspondent, document_type, tags, created date, custom
// fields, OCR-extracted content) lands in EntryRecord.DomainMetadata
// where the Library Metadata UI surfaces it.
//
// Auth: Paperless uses `Authorization: Token <api_token>` (NOT
// `Bearer`). The token is created in Paperless via the user dropdown
// → "My Profile" → "Create Auth Token".
//
// Known limitations in v0.7.0 (deferred to a follow-up PR):
//   - ReadFile returns ErrUnsupported. Content preview in the entry
//     drawer needs a stable per-entry native id; that lands with the
//     Tier 1 CC2 work and unlocks paperless content fetch as a free
//     follow-up.
//   - Title collisions within the same correspondent/doc-type/year
//     overwrite each other (last-write-wins on the unique
//     (source, path) constraint). Paperless titles are typically
//     unique-by-month so this is rare, but worth tracking.
//   - WalkShallow is not implemented; paperless single-walks. Scan
//     concurrency wouldn't help — the bottleneck is Paperless's API,
//     not akashic's ingest.
type PaperlessConnector struct {
	baseURL    string
	apiToken   string
	tagFilter  []string // optional case-insensitive whitelist
	tlsVerify  bool
	pageSize   int

	httpClient *http.Client

	// Lookup tables filled by Connect(). Paperless documents reference
	// correspondents / document_types / tags by integer id, so we
	// resolve them once up-front rather than per-document.
	correspondents map[int]string
	documentTypes  map[int]string
	tags           map[int]string
}

// NewPaperlessConnector builds a connector but does NOT issue any HTTP
// requests yet — that happens in Connect().
func NewPaperlessConnector(rawURL, apiToken string, tagFilter []string, tlsVerify bool) *PaperlessConnector {
	return &PaperlessConnector{
		baseURL:   strings.TrimRight(rawURL, "/"),
		apiToken:  apiToken,
		tagFilter: tagFilter,
		tlsVerify: tlsVerify,
		pageSize:  100,
	}
}

func (c *PaperlessConnector) Type() string { return "paperless" }

func (c *PaperlessConnector) Connect(ctx context.Context) error {
	if c.baseURL == "" {
		return fmt.Errorf("paperless: url is required")
	}
	if c.apiToken == "" {
		return fmt.Errorf("paperless: api_token is required")
	}
	c.httpClient = &http.Client{
		Timeout: 30 * time.Second,
		// TLS verification is on by default; users can opt out via
		// `tls_verify: false` for self-signed installs. Threading the
		// custom transport through `http.Transport.TLSClientConfig`
		// rather than a tls.Config{} so we keep H/2 + connection-reuse
		// defaults that would otherwise be lost.
		Transport: defaultTransport(c.tlsVerify),
	}

	// Smoke-test auth + load lookup tables. /api/profile/ would also
	// work, but ?page_size=1 against /api/documents/ exercises the
	// endpoint we'll actually scan with — same auth rejection mode,
	// same pagination shape — so a green probe is a stronger signal.
	if _, err := c.fetchJSON(ctx, "/api/documents/?page_size=1"); err != nil {
		return fmt.Errorf("paperless: connect: %w", err)
	}

	corrs, err := c.loadLookup(ctx, "/api/correspondents/")
	if err != nil {
		return fmt.Errorf("paperless: load correspondents: %w", err)
	}
	c.correspondents = corrs

	docTypes, err := c.loadLookup(ctx, "/api/document_types/")
	if err != nil {
		return fmt.Errorf("paperless: load document_types: %w", err)
	}
	c.documentTypes = docTypes

	tags, err := c.loadLookup(ctx, "/api/tags/")
	if err != nil {
		return fmt.Errorf("paperless: load tags: %w", err)
	}
	c.tags = tags

	return nil
}

// Walk paginates the document list and emits a synthetic directory
// hierarchy. excludePatterns and computeHash are accepted for
// interface compatibility but ignored — paperless has no path
// segments to exclude (the synthesis is fully derived from server-
// side metadata) and Paperless already provides per-document checksums
// via the API.
func (c *PaperlessConnector) Walk(
	ctx context.Context, root string, _ []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	var stats walker.WalkStats
	if c.httpClient == nil {
		return stats, fmt.Errorf("paperless: not connected")
	}

	emittedDirs := map[string]bool{"/": true}
	emitDir := func(dirPath string) error {
		if dirPath == "" || dirPath == "/" {
			return nil
		}
		if emittedDirs[dirPath] {
			return nil
		}
		emittedDirs[dirPath] = true
		return fn(&models.EntryRecord{
			Path: dirPath,
			Name: pathBase(dirPath),
			Kind: "directory",
		})
	}

	// Tag whitelist: case-insensitive set; empty means "all".
	var tagFilterSet map[string]bool
	if len(c.tagFilter) > 0 {
		tagFilterSet = make(map[string]bool, len(c.tagFilter))
		for _, t := range c.tagFilter {
			tagFilterSet[strings.ToLower(strings.TrimSpace(t))] = true
		}
	}

	// Paperless paginates with `?page=N&page_size=K`. The response carries
	// `next` as a fully-qualified URL or null; we follow it until null.
	next := fmt.Sprintf("/api/documents/?page=1&page_size=%d", c.pageSize)
	for next != "" {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		body, err := c.fetchJSON(ctx, next)
		if err != nil {
			return stats, err
		}
		var page paperlessDocumentsPage
		if err := json.Unmarshal(body, &page); err != nil {
			return stats, fmt.Errorf("paperless: decode page: %w", err)
		}
		for _, doc := range page.Results {
			if err := ctx.Err(); err != nil {
				return stats, err
			}
			tagNames := c.resolveTags(doc.Tags)
			if tagFilterSet != nil && !tagsIntersect(tagNames, tagFilterSet) {
				continue
			}
			entry := c.buildEntry(doc, tagNames)
			// Emit each parent path component (correspondent, then
			// correspondent/doc_type, then correspondent/doc_type/year)
			// the first time we see it. The dedup map keeps re-emits
			// out so the API ingest doesn't churn versions on
			// directory rows that didn't actually change.
			for _, ancestor := range ancestorPaths(entry.Path) {
				if err := emitDir(ancestor); err != nil {
					return stats, err
				}
			}
			if err := fn(entry); err != nil {
				return stats, err
			}
		}
		next = nextPath(page.Next, c.baseURL)
	}
	_ = root // accepted for interface; paperless walk always covers the whole library
	return stats, nil
}

// ReadFile is not implemented in v0.7.0 — see the package-level note
// on missing native-id plumbing. Returns a sentinel that the api maps
// to a 501 in the entry-content endpoint.
func (c *PaperlessConnector) ReadFile(_ context.Context, _ string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("paperless: ReadFile not supported (content fetch lands with the Tier 1 native-id work)")
}

// Delete is intentionally unimplemented. The Duplicates flow is the
// only akashic surface that calls Delete on a connector, and deleting
// documents from Paperless via an indexer would be surprising
// behaviour. Removing the document in Paperless directly is the
// expected workflow.
func (c *PaperlessConnector) Delete(_ context.Context, _ string) error {
	return fmt.Errorf("paperless: Delete not supported")
}

func (c *PaperlessConnector) Close() error { return nil }

// ----- helpers -----

func (c *PaperlessConnector) buildEntry(doc paperlessDocument, tagNames []string) *models.EntryRecord {
	correspondent := lookupOr(c.correspondents, doc.Correspondent, "Unsorted")
	documentType := lookupOr(c.documentTypes, doc.DocumentType, "Unfiled")

	year := "Undated"
	if !doc.Created.IsZero() {
		year = strconv.Itoa(doc.Created.Year())
	}

	title := strings.TrimSpace(doc.Title)
	if title == "" {
		title = fmt.Sprintf("document-%d", doc.ID)
	}
	ext := docExtension(doc)
	filename := safeSegment(title) + "." + ext
	parentPath := "/" + safeSegment(correspondent) +
		"/" + safeSegment(documentType) +
		"/" + safeSegment(year)
	fullPath := parentPath + "/" + filename

	entry := &models.EntryRecord{
		Path:      fullPath,
		Name:      filename,
		Kind:      "file",
		Extension: ext,
	}
	if doc.Modified != nil && !doc.Modified.IsZero() {
		t := *doc.Modified
		entry.ModifiedAt = &t
	}
	if !doc.Created.IsZero() {
		t := doc.Created
		entry.CreatedAt = &t
	}
	if doc.ContentChecksum != "" {
		entry.ContentHash = doc.ContentChecksum
	}

	dm := map[string]interface{}{
		"paperless_id":  doc.ID,
		"correspondent": correspondent,
		"document_type": documentType,
		"created":       doc.Created.Format(time.RFC3339),
		"title":         title,
	}
	if doc.ArchiveSerialNumber != nil {
		dm["archive_serial_number"] = *doc.ArchiveSerialNumber
	}
	if doc.OriginalFileName != "" {
		dm["original_filename"] = doc.OriginalFileName
	}
	if len(tagNames) > 0 {
		dm["tags"] = tagNames
	}
	if len(doc.CustomFields) > 0 {
		// Each custom_fields entry is `{field: int, value: any}`.
		// Surfacing the field id as-is is unhelpful; the names live in
		// /api/custom_fields/ which we don't preload (most installs
		// don't define any). Pass through the raw value list so it's
		// at least present and queryable; cleaner naming lands when
		// custom_fields lookup is added.
		raw := make([]map[string]interface{}, 0, len(doc.CustomFields))
		for _, cf := range doc.CustomFields {
			raw = append(raw, map[string]interface{}{
				"field": cf.Field,
				"value": cf.Value,
			})
		}
		dm["custom_fields"] = raw
	}
	if doc.Content != "" {
		// The OCR text is gold for search but the JSONB column is the
		// wrong place for tens of KB of body content per row — the
		// content_text path through Tika exists for that. Truncate to
		// a preview so the Library Metadata section can show "first
		// few lines" without bloating the row.
		dm["content_preview"] = truncate(doc.Content, 480)
	}
	entry.DomainMetadata = dm
	return entry
}

func (c *PaperlessConnector) resolveTags(ids []int) []string {
	if len(ids) == 0 {
		return nil
	}
	out := make([]string, 0, len(ids))
	for _, id := range ids {
		if name, ok := c.tags[id]; ok && name != "" {
			out = append(out, name)
		}
	}
	return out
}

func (c *PaperlessConnector) fetchJSON(ctx context.Context, pathOrAbs string) ([]byte, error) {
	target := pathOrAbs
	if strings.HasPrefix(target, "/") {
		target = c.baseURL + target
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	// "Token <api_token>" — Paperless quirk; rejects "Bearer".
	req.Header.Set("Authorization", "Token "+c.apiToken)
	req.Header.Set("Accept", "application/json")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("paperless: auth rejected (%d): %s", resp.StatusCode, snippet(body))
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("paperless: GET %s: %d %s", target, resp.StatusCode, snippet(body))
	}
	return body, nil
}

// loadLookup fetches every page of a paginated lookup endpoint and
// returns an id → name map. Used at Connect() time for the three
// reference tables (correspondents, document_types, tags).
func (c *PaperlessConnector) loadLookup(ctx context.Context, endpoint string) (map[int]string, error) {
	out := map[int]string{}
	next := fmt.Sprintf("%s?page_size=%d", endpoint, c.pageSize)
	for next != "" {
		body, err := c.fetchJSON(ctx, next)
		if err != nil {
			return nil, err
		}
		var page paperlessLookupPage
		if err := json.Unmarshal(body, &page); err != nil {
			return nil, fmt.Errorf("decode lookup %s: %w", endpoint, err)
		}
		for _, item := range page.Results {
			out[item.ID] = item.Name
		}
		next = nextPath(page.Next, c.baseURL)
	}
	return out, nil
}

// nextPath strips the API host from a Paperless `next` URL (which is
// always absolute) so callers can hand it back to fetchJSON which
// re-prepends baseURL. Returns "" when there is no next page.
func nextPath(rawNext, base string) string {
	if rawNext == "" {
		return ""
	}
	u, err := url.Parse(rawNext)
	if err != nil {
		return ""
	}
	tail := u.Path
	if u.RawQuery != "" {
		tail += "?" + u.RawQuery
	}
	_ = base
	return tail
}

// pathBase returns the last segment of a "/"-separated path. Used to
// populate EntryRecord.Name on directory rows.
func pathBase(p string) string {
	p = strings.TrimRight(p, "/")
	if i := strings.LastIndex(p, "/"); i >= 0 {
		return p[i+1:]
	}
	return p
}

// ancestorPaths returns the chain of parent paths from "/" down to
// the file's parent, excluding the file itself. e.g.
//
//	"/IRS/Tax forms/2024/W-2.pdf"
//	  → ["/IRS", "/IRS/Tax forms", "/IRS/Tax forms/2024"]
//
// The order matters — the API ingest path emits directory rows in
// insertion order, and we want shallow ancestors first so subtree
// rollups in scan_runner traverse top-down.
func ancestorPaths(filePath string) []string {
	parts := strings.Split(strings.Trim(filePath, "/"), "/")
	if len(parts) <= 1 {
		return nil
	}
	out := make([]string, 0, len(parts)-1)
	cur := ""
	for _, p := range parts[:len(parts)-1] {
		cur += "/" + p
		out = append(out, cur)
	}
	return out
}

// safeSegment sanitises a string for use as a single path segment.
// Replaces "/" (path separator) and control chars with "_". Keeps the
// rest as-is so user-friendly characters like spaces and unicode
// survive into the Browse UI.
func safeSegment(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "_"
	}
	var b strings.Builder
	b.Grow(len(s))
	for _, r := range s {
		switch {
		case r == '/' || r == '\\' || r == 0:
			b.WriteByte('_')
		case r < 0x20:
			b.WriteByte('_')
		default:
			b.WriteRune(r)
		}
	}
	return b.String()
}

// docExtension picks the file extension to advertise for a document.
// Paperless stores documents as PDFs after archive normalisation, but
// the original may have been .docx / .png / .tiff / etc. We surface
// the original extension when present so the Browse UI's "Filter
// by extension" chip lands on something the user recognises.
func docExtension(doc paperlessDocument) string {
	if doc.OriginalFileName != "" {
		if i := strings.LastIndex(doc.OriginalFileName, "."); i >= 0 && i+1 < len(doc.OriginalFileName) {
			ext := strings.ToLower(doc.OriginalFileName[i+1:])
			if ext != "" {
				return ext
			}
		}
	}
	return "pdf"
}

func lookupOr(m map[int]string, key *int, fallback string) string {
	if key == nil {
		return fallback
	}
	if v, ok := m[*key]; ok && v != "" {
		return v
	}
	return fallback
}

func tagsIntersect(have []string, allow map[string]bool) bool {
	for _, t := range have {
		if allow[strings.ToLower(t)] {
			return true
		}
	}
	return false
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

func snippet(b []byte) string {
	const max = 200
	if len(b) <= max {
		return string(b)
	}
	return string(b[:max]) + "…"
}

func defaultTransport(tlsVerify bool) http.RoundTripper {
	t := http.DefaultTransport.(*http.Transport).Clone()
	if !tlsVerify {
		// Self-signed paperless installs are common on home setups.
		// The validation gap is opt-in via tls_verify=false; UI
		// surfaces a warning when the toggle is off.
		// #nosec G402 — opt-in by user; gated behind a per-source toggle.
		t.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	return t
}

// ----- API response shapes -----

type paperlessDocumentsPage struct {
	Count   int                 `json:"count"`
	Next    string              `json:"next"`
	Results []paperlessDocument `json:"results"`
}

type paperlessLookupPage struct {
	Count   int               `json:"count"`
	Next    string            `json:"next"`
	Results []paperlessLookup `json:"results"`
}

type paperlessLookup struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

// paperlessDocument mirrors the fields we need from Paperless's
// /api/documents/ response. Fields the connector doesn't use are
// elided so a future Paperless version that adds keys doesn't break
// the decoder.
type paperlessDocument struct {
	ID                  int                       `json:"id"`
	Title               string                    `json:"title"`
	Content             string                    `json:"content"`
	Tags                []int                     `json:"tags"`
	Correspondent       *int                      `json:"correspondent"`
	DocumentType        *int                      `json:"document_type"`
	Created             time.Time                 `json:"created"`
	Modified            *time.Time                `json:"modified"`
	ArchiveSerialNumber *string                   `json:"archive_serial_number"`
	OriginalFileName    string                    `json:"original_file_name"`
	ContentChecksum     string                    `json:"checksum"`
	CustomFields        []paperlessCustomFieldVal `json:"custom_fields"`
}

type paperlessCustomFieldVal struct {
	Field int         `json:"field"`
	Value interface{} `json:"value"`
}
