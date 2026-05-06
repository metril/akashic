package connector

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"path/filepath"
	"strings"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

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
//   - Album memberships require fetching `/api/album/{id}` per album
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
	// /api/album + /api/album/{id} fan-out. Empty when the user
	// hasn't created any albums.
	assetAlbums map[string][]string
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

	// Smoke-test auth via /api/server-info/ping. Cheaper than a search
	// roundtrip and immich-specific (returns "{"res":"pong"}").
	if _, err := c.fetchJSON(ctx, http.MethodGet, "/api/server-info/ping", nil); err != nil {
		return fmt.Errorf("immich: connect: %w", err)
	}

	// Pre-load albums so the per-asset album-name lookup is O(1) at
	// walk time. List endpoint returns just the album metadata; the
	// per-album endpoint includes the asset list.
	body, err := c.fetchJSON(ctx, http.MethodGet, "/api/album", nil)
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
		full, err := c.fetchJSON(ctx, http.MethodGet, "/api/album/"+a.ID, nil)
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
			return stats, err
		}
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
		entry.ContentHash = asset.Checksum
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

func (c *ImmichConnector) fetchJSON(ctx context.Context, method, path string, body interface{}) ([]byte, error) {
	target := path
	if strings.HasPrefix(target, "/") {
		target = c.baseURL + target
	}
	var bodyReader *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		bodyReader = bytes.NewReader(raw)
	}
	var req *http.Request
	var err error
	if bodyReader != nil {
		req, err = http.NewRequestWithContext(ctx, method, target, bodyReader)
	} else {
		req, err = http.NewRequestWithContext(ctx, method, target, nil)
	}
	if err != nil {
		return nil, err
	}
	req.Header.Set("x-api-key", c.apiKey)
	req.Header.Set("Accept", "application/json")
	if bodyReader != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden {
		return nil, fmt.Errorf("immich: auth rejected (%d): %s", resp.StatusCode, snippet(respBody))
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("immich: %s %s: %d %s", method, target, resp.StatusCode, snippet(respBody))
	}
	return respBody, nil
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
