package connector

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math/rand"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// v0.39.0 — retry knobs for fetchJSON. Mirrors client.SendBatch
// (4 attempts, 250ms base, exponential with ±25% jitter); a single
// transient Immich blip used to fail the whole scan after the walker
// had already paginated through tens of thousands of assets.
//
// `var` rather than `const` so the test suite can shrink the backoff
// to keep retry-exhausted scenarios from dominating wall-clock time.
var (
	immichMaxAttempts = 4
	immichBackoffBase = 250 * time.Millisecond
)

// v0.40.0 — cap consecutive skipped pages before the Walk gives up.
// A single failing page on `/api/search/metadata` is the per-row-
// corruption fingerprint (Immich #24359 — Postgres TOAST corruption
// on a specific asset row); skipping it lets the rest of the library
// index. Three in a row, though, is Immich actually being down and
// we should not silently lose hundreds of assets.
const immichMaxConsecutivePageFailures = 3

// retryableHTTPError is returned by fetchJSONOnce for 408/429/5xx
// responses. fetchJSON's retry-exhaustion path wraps it via `%w` so
// callers can errors.As to recover the status code. Walk uses this
// to distinguish "one bad page upstream" (skip and continue) from
// any other terminal error (return immediately).
type retryableHTTPError struct {
	StatusCode int
	Snippet    string
	Method     string
	URL        string
}

func (e *retryableHTTPError) Error() string {
	return fmt.Sprintf("immich: %s %s: %d %s", e.Method, e.URL, e.StatusCode, e.Snippet)
}

// ImmichConnector indexes an Immich photo/video library via its REST
// API. Tier 3 / v0.8.0. Same hostless shape as PaperlessConnector:
// the URL + api_key live on the source's connection_config; no Host
// row.
//
// Walk strategy: paginate `POST /api/search/metadata` with
// {"page": N, "size": 250}. For each asset, emit a synthetic path
// `/All Photos/<yyyy>/<mm>/<assetId>-<filename>` keyed off the asset's
// `fileCreatedAt` so browse navigation reads as a chronological
// hierarchy. Album memberships flow into `domain_metadata.album` as a
// multi-valued list (filterable via the Library Metadata facet
// panel) rather than into the synthetic path itself — moving photos
// between albums in Immich would otherwise rewrite paths and break
// browse navigation in akashic.
//
// Auth: Immich uses `x-api-key: <key>` (NOT `Authorization: Bearer`).
// Users create a key in Immich under Account Settings → API Keys.
//
// Known limitations in v0.8.0:
//   - ReadFile returns "not supported" for the same reason paperless
//     does — content fetch waits on the Tier 1 native-id work.
//   - Immich `/api/people` is not loaded; per-asset `people` comes
//     from the inline asset shape if the version exposes it. Older
//     Immich versions may surface no person data.
//   - Album memberships require fetching `/api/albums/{id}` per album
//     to enumerate the assets in it. We fetch them all up-front at
//     Connect time — slow for libraries with hundreds of albums but
//     acceptable for typical home installs.
type ImmichConnector struct {
	baseURL         string
	apiKey          string
	albumFilter     []string // optional case-insensitive whitelist by album NAME
	includeArchived bool
	tlsVerify       bool
	pageSize        int

	httpClient *http.Client

	// asset_id → list of album names. Populated by Connect() via
	// /api/albums + /api/albums/{id} fan-out. Empty when the user
	// hasn't created any albums.
	assetAlbums map[string][]string

	// v0.40.0 — optional UI-visible warn sink. Wired by the scanner
	// (via the warnHookSetter interface) so page-skip notifications
	// reach the scan log, not just docker logs. Nil-safe.
	warnHook func(format string, args ...any)
}

// SetWarnHook registers a callback the connector calls for
// UI-visible warnings (e.g., "page N failed, skipping ~250 assets").
// The scanner package detects this method via a small interface and
// supplies its s.warn. If unset, warnings still hit docker logs via
// log.Printf inside the warn() helper below.
//
// v0.40.0 — added to surface the skip-on-upstream-5xx outcome
// without coupling the Connector interface to the scanner's
// log sink for every connector type.
func (c *ImmichConnector) SetWarnHook(fn func(format string, args ...any)) {
	c.warnHook = fn
}

func (c *ImmichConnector) warn(format string, args ...any) {
	if c.warnHook != nil {
		c.warnHook(format, args...)
	}
	log.Printf("immich: "+format, args...)
}

// NewImmichConnector builds the connector but does NOT issue any HTTP
// requests yet — that happens in Connect(). pageSize defaults to 250
// to match Immich's typical server-side page cap.
func NewImmichConnector(rawURL, apiKey string, albumFilter []string, includeArchived, tlsVerify bool) *ImmichConnector {
	return &ImmichConnector{
		baseURL:         strings.TrimRight(rawURL, "/"),
		apiKey:          apiKey,
		albumFilter:     albumFilter,
		includeArchived: includeArchived,
		tlsVerify:       tlsVerify,
		pageSize:        250,
		assetAlbums:     map[string][]string{},
	}
}

func (c *ImmichConnector) Type() string { return "immich" }

func (c *ImmichConnector) Connect(ctx context.Context) error {
	if c.baseURL == "" {
		return fmt.Errorf("immich: url is required")
	}
	if c.apiKey == "" {
		return fmt.Errorf("immich: api_key is required")
	}
	c.httpClient = &http.Client{
		Timeout:   30 * time.Second,
		Transport: immichTransport(c.tlsVerify),
	}

	// Smoke-test auth via /api/users/me. Validates both server
	// reachability and the API key in a single roundtrip. The older
	// /api/server-info/ping was renamed to /api/server/ping and made
	// unauthenticated (PR #20250 "feat!: more permissions"), so we'd
	// have no auth signal at connect time if we used it — a wrong key
	// would surface as a confusing 401 mid-Walk against /api/albums.
	if _, err := c.fetchJSON(ctx, http.MethodGet, "/api/users/me", nil); err != nil {
		return fmt.Errorf("immich: connect: %w", err)
	}

	// Pre-load albums so the per-asset album-name lookup is O(1) at
	// walk time. List endpoint returns just the album metadata; the
	// per-album endpoint includes the asset list.
	body, err := c.fetchJSON(ctx, http.MethodGet, "/api/albums", nil)
	if err != nil {
		return fmt.Errorf("immich: list albums: %w", err)
	}
	var albums []immichAlbumRef
	if err := json.Unmarshal(body, &albums); err != nil {
		return fmt.Errorf("immich: decode albums: %w", err)
	}
	for _, a := range albums {
		if err := ctx.Err(); err != nil {
			return err
		}
		full, err := c.fetchJSON(ctx, http.MethodGet, "/api/albums/"+a.ID, nil)
		if err != nil {
			// One unreachable album shouldn't tank the entire scan.
			// Drop it from the membership map; assets in that album
			// just won't surface its name as a domain_metadata
			// value. Failure is logged via the error chain on
			// the api side.
			continue
		}
		var detail immichAlbumDetail
		if err := json.Unmarshal(full, &detail); err != nil {
			continue
		}
		for _, asset := range detail.Assets {
			c.assetAlbums[asset.ID] = append(c.assetAlbums[asset.ID], a.AlbumName)
		}
	}
	return nil
}

// Walk paginates the asset list and emits a synthetic chronological
// hierarchy. Excludes archived assets unless `include_archived` is set
// on the source.
func (c *ImmichConnector) Walk(
	ctx context.Context, root string, _ []string, _ bool, _ bool,
	fn func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	var stats walker.WalkStats
	if c.httpClient == nil {
		return stats, fmt.Errorf("immich: not connected")
	}

	emittedDirs := map[string]bool{"/": true}
	emitDir := func(dirPath string) error {
		if dirPath == "" || dirPath == "/" || emittedDirs[dirPath] {
			return nil
		}
		emittedDirs[dirPath] = true
		return fn(&models.EntryRecord{
			Path: dirPath,
			Name: pathBase(dirPath),
			Kind: "directory",
		})
	}

	var albumWhitelist map[string]bool
	if len(c.albumFilter) > 0 {
		albumWhitelist = make(map[string]bool, len(c.albumFilter))
		for _, a := range c.albumFilter {
			albumWhitelist[strings.ToLower(strings.TrimSpace(a))] = true
		}
	}

	// v0.40.0 — track consecutive page failures so we can distinguish
	// "one poisoned row upstream" (skip) from "Immich actually went
	// down" (abort). Reset on every successful page.
	var consecutivePageFailures int

	for page := 1; ; page++ {
		if err := ctx.Err(); err != nil {
			return stats, err
		}
		reqBody := map[string]interface{}{
			"page": page,
			"size": c.pageSize,
		}
		// `withArchived: false` matches the Immich UI default — most
		// users archive in Immich precisely to keep stuff out of
		// the photo grid, and inheriting that behaviour avoids
		// surprising users with archived assets in akashic.
		if !c.includeArchived {
			reqBody["withArchived"] = false
		} else {
			reqBody["withArchived"] = true
		}
		body, err := c.fetchJSON(ctx, http.MethodPost, "/api/search/metadata", reqBody)
		if err != nil {
			// v0.40.0 — when /api/search/metadata returns 5xx after
			// retry exhaustion on a non-first page, skip just that
			// page and keep walking. This handles the documented
			// upstream-Immich pathology (Immich #24359 — TOAST
			// corruption on a specific asset row deterministically
			// throws "Failed to search assets" on the page that
			// contains it) without dropping the remaining ~hundreds
			// of healthy pages. Page 1 failures stay fatal — that's
			// Immich genuinely unavailable, not a bad row. Three
			// consecutive skips abort: that's an outage, not
			// corruption.
			var rhe *retryableHTTPError
			if errors.As(err, &rhe) && rhe.StatusCode >= 500 && page > 1 {
				consecutivePageFailures++
				if consecutivePageFailures >= immichMaxConsecutivePageFailures {
					return stats, fmt.Errorf(
						"immich: %d consecutive page failures; aborting scan: %w",
						consecutivePageFailures, err)
				}
				stats.UpstreamPagesSkipped++
				c.warn(
					"page %d failed (HTTP %d: %s); skipping ~%d assets and continuing",
					page, rhe.StatusCode, rhe.Snippet, c.pageSize,
				)
				continue
			}
			return stats, err
		}
		consecutivePageFailures = 0
		var resp immichSearchResponse
		if err := json.Unmarshal(body, &resp); err != nil {
			return stats, fmt.Errorf("immich: decode search page: %w", err)
		}
		for _, asset := range resp.Assets.Items {
			if err := ctx.Err(); err != nil {
				return stats, err
			}
			albumNames := c.assetAlbums[asset.ID]
			if albumWhitelist != nil {
				match := false
				for _, n := range albumNames {
					if albumWhitelist[strings.ToLower(n)] {
						match = true
						break
					}
				}
				if !match {
					continue
				}
			}
			entry := c.buildEntry(asset, albumNames)
			for _, ancestor := range ancestorPaths(entry.Path) {
				if err := emitDir(ancestor); err != nil {
					return stats, err
				}
			}
			if err := fn(entry); err != nil {
				return stats, err
			}
		}
		// Immich paginates on `nextPage` (a string page number, or
		// empty/null when exhausted).
		if resp.Assets.NextPage == "" {
			break
		}
	}
	_ = root
	return stats, nil
}

// ReadFile is not implemented in v0.8.0 — see PaperlessConnector for
// the same explanation. Returns a sentinel mapped to a 501.
func (c *ImmichConnector) ReadFile(_ context.Context, _ string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("immich: ReadFile not supported (content fetch lands with the Tier 1 native-id work)")
}

// Delete is intentionally unimplemented — same reasoning as
// PaperlessConnector.
func (c *ImmichConnector) Delete(_ context.Context, _ string) error {
	return fmt.Errorf("immich: Delete not supported")
}

func (c *ImmichConnector) Close() error { return nil }

// ----- helpers -----

func (c *ImmichConnector) buildEntry(asset immichAsset, albumNames []string) *models.EntryRecord {
	// Path keying: "fileCreatedAt" is the file system mtime as Immich
	// recorded it on import. Falls back to "exifInfo.dateTimeOriginal"
	// (camera shutter time) when fileCreatedAt is missing. Falls back
	// further to "Undated" when neither is set.
	when := asset.FileCreatedAt
	if when.IsZero() && asset.ExifInfo != nil && !asset.ExifInfo.DateTimeOriginal.IsZero() {
		when = asset.ExifInfo.DateTimeOriginal
	}
	year := "Undated"
	month := "00"
	if !when.IsZero() {
		year = fmt.Sprintf("%04d", when.Year())
		month = fmt.Sprintf("%02d", int(when.Month()))
	}

	// Filename: prefix with a short asset-id hash so two photos with
	// the same originalFileName in the same year/month don't clobber
	// each other on the unique (source, path) constraint. Eight chars
	// of the UUID is collision-resistant in practice for personal
	// libraries while keeping browse readable.
	original := asset.OriginalFileName
	if original == "" {
		original = "asset"
	}
	idPrefix := asset.ID
	if len(idPrefix) > 8 {
		idPrefix = idPrefix[:8]
	}
	ext := strings.TrimPrefix(filepath.Ext(original), ".")
	if ext == "" {
		ext = "jpg"
	}
	base := stripExt(original) + "-" + idPrefix + "." + ext
	parentPath := "/All Photos/" + safeSegment(year) + "/" + safeSegment(month)
	fullPath := parentPath + "/" + safeSegment(base)

	entry := &models.EntryRecord{
		Path:      fullPath,
		Name:      safeSegment(base),
		Kind:      "file",
		Extension: strings.ToLower(ext),
	}
	if !when.IsZero() {
		t := when
		entry.ModifiedAt = &t
	}
	if !asset.FileCreatedAt.IsZero() {
		t := asset.FileCreatedAt
		entry.CreatedAt = &t
	}
	if asset.Checksum != "" {
		// Immich stores SHA1 of the file. Prefix to match the
		// namespace convention used elsewhere (sha1:, md5:, sha256:,
		// etag:, dropbox:, quickxor:) — review notable.
		entry.ContentHash = "sha1:" + strings.ToLower(asset.Checksum)
	}

	dm := map[string]interface{}{
		"immich_id":         asset.ID,
		"original_filename": asset.OriginalFileName,
	}
	if asset.OriginalPath != "" {
		dm["original_path"] = asset.OriginalPath
	}
	if !when.IsZero() {
		dm["datetime_original"] = when.Format(time.RFC3339)
	}
	if len(albumNames) > 0 {
		dm["album"] = albumNames
	}
	if exif := asset.ExifInfo; exif != nil {
		if exif.Make != "" {
			dm["camera_make"] = exif.Make
		}
		if exif.Model != "" {
			dm["camera_model"] = exif.Model
		}
		if exif.Latitude != nil && exif.Longitude != nil {
			dm["gps_lat"] = *exif.Latitude
			dm["gps_lng"] = *exif.Longitude
		}
		if exif.ImageWidth != 0 && exif.ImageHeight != 0 {
			dm["image_width"] = exif.ImageWidth
			dm["image_height"] = exif.ImageHeight
		}
	}
	if len(asset.People) > 0 {
		names := make([]string, 0, len(asset.People))
		for _, p := range asset.People {
			if p.Name != "" {
				names = append(names, p.Name)
			}
		}
		if len(names) > 0 {
			dm["person"] = names
		}
	}
	entry.DomainMetadata = dm
	return entry
}

// fetchJSON wraps fetchJSONOnce in a retry loop. Transient transport
// errors and 408/429/5xx responses are retried with exponential
// backoff; auth (401/403) and other 4xx are terminal. Caller-driven
// context cancellation short-circuits without consuming the budget.
func (c *ImmichConnector) fetchJSON(ctx context.Context, method, path string, body interface{}) ([]byte, error) {
	target := path
	if strings.HasPrefix(target, "/") {
		target = c.baseURL + target
	}
	var rawBody []byte
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rawBody = raw
	}

	var lastErr error
	for attempt := 0; attempt < immichMaxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		respBody, retryable, err := c.fetchJSONOnce(ctx, method, target, rawBody)
		if err == nil {
			return respBody, nil
		}
		lastErr = err
		if !retryable {
			return nil, err
		}
		// Exponential backoff with ±25% jitter, mirroring SendBatch.
		// Cancellable so a SIGTERM or a heartbeat 409 mid-backoff
		// doesn't stall the scan for ~2 s.
		if attempt+1 < immichMaxAttempts {
			d := immichBackoffBase << attempt
			jitter := time.Duration(rand.Int63n(int64(d / 4)))
			select {
			case <-ctx.Done():
				return nil, ctx.Err()
			case <-time.After(d + jitter):
			}
		}
	}
	return nil, fmt.Errorf("immich: %d attempts failed: %w", immichMaxAttempts, lastErr)
}

// fetchJSONOnce performs one HTTP attempt. Returns (body, retryable, err)
// where retryable indicates whether fetchJSON should back off and retry.
func (c *ImmichConnector) fetchJSONOnce(ctx context.Context, method, target string, rawBody []byte) ([]byte, bool, error) {
	var bodyReader *bytes.Reader
	if rawBody != nil {
		bodyReader = bytes.NewReader(rawBody)
	}
	var req *http.Request
	var err error
	if bodyReader != nil {
		req, err = http.NewRequestWithContext(ctx, method, target, bodyReader)
	} else {
		req, err = http.NewRequestWithContext(ctx, method, target, nil)
	}
	if err != nil {
		return nil, false, err
	}
	req.Header.Set("x-api-key", c.apiKey)
	req.Header.Set("Accept", "application/json")
	if bodyReader != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		// Distinguish caller-cancellation (not retryable; the scan
		// is ending) from genuine transport blips (TCP RST, DNS,
		// TLS handshake, server-half-close mid-request — all
		// worth retrying).
		if ctx.Err() != nil {
			return nil, false, ctx.Err()
		}
		return nil, true, err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		if ctx.Err() != nil {
			return nil, false, ctx.Err()
		}
		return nil, true, err
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, false, fmt.Errorf("immich: auth rejected (%d): %s", resp.StatusCode, snippet(respBody))
	}
	// 408 Request Timeout, 429 Too Many Requests, and any 5xx are
	// transient by definition. Backing off and retrying is the right
	// thing — and matches what client.SendBatch does for the api side.
	//
	// v0.40.0 — returned as a typed retryableHTTPError so Walk can
	// errors.As the retry-exhausted form and decide whether to skip
	// a single bad page vs. fail the whole scan.
	if resp.StatusCode == http.StatusRequestTimeout ||
		resp.StatusCode == http.StatusTooManyRequests ||
		resp.StatusCode >= 500 {
		return nil, true, &retryableHTTPError{
			StatusCode: resp.StatusCode,
			Snippet:    snippet(respBody),
			Method:     method,
			URL:        target,
		}
	}
	if resp.StatusCode >= 400 {
		return nil, false, fmt.Errorf("immich: %s %s: %d %s", method, target, resp.StatusCode, snippet(respBody))
	}
	return respBody, false, nil
}

func immichTransport(tlsVerify bool) http.RoundTripper {
	t := http.DefaultTransport.(*http.Transport).Clone()
	if !tlsVerify {
		// #nosec G402 — opt-in by user; gated behind a per-source
		// `tls_verify=false` toggle for self-signed home installs.
		t.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	return t
}

func stripExt(s string) string {
	if i := strings.LastIndex(s, "."); i >= 0 {
		return s[:i]
	}
	return s
}

// ----- API response shapes -----

type immichSearchResponse struct {
	Assets struct {
		NextPage string         `json:"nextPage"`
		Items    []immichAsset  `json:"items"`
	} `json:"assets"`
}

type immichAsset struct {
	ID               string           `json:"id"`
	OriginalFileName string           `json:"originalFileName"`
	OriginalPath     string           `json:"originalPath"`
	FileCreatedAt    time.Time        `json:"fileCreatedAt"`
	Checksum         string           `json:"checksum"`
	ExifInfo         *immichExifInfo  `json:"exifInfo"`
	People           []immichPersonRef `json:"people"`
}

type immichExifInfo struct {
	DateTimeOriginal time.Time `json:"dateTimeOriginal"`
	Make             string    `json:"make"`
	Model            string    `json:"model"`
	Latitude         *float64  `json:"latitude"`
	Longitude        *float64  `json:"longitude"`
	ImageWidth       int       `json:"exifImageWidth"`
	ImageHeight      int       `json:"exifImageHeight"`
}

type immichPersonRef struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

type immichAlbumRef struct {
	ID        string `json:"id"`
	AlbumName string `json:"albumName"`
}

type immichAlbumDetail struct {
	ID        string         `json:"id"`
	AlbumName string         `json:"albumName"`
	Assets    []immichAssetID `json:"assets"`
}

type immichAssetID struct {
	ID string `json:"id"`
}
