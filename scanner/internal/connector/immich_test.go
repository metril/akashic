package connector

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestStripExt(t *testing.T) {
	cases := map[string]string{
		"":             "",
		"plain":        "plain",
		"a.jpg":        "a",
		"path/to/x.y":  "path/to/x",
		"weird.tar.gz": "weird.tar",
	}
	for in, want := range cases {
		if got := stripExt(in); got != want {
			t.Errorf("stripExt(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestImmichBuildEntry(t *testing.T) {
	c := &ImmichConnector{}
	lat, lng := 37.7749, -122.4194
	created := time.Date(2024, 9, 16, 14, 30, 0, 0, time.UTC)
	asset := immichAsset{
		ID:               "abc12345-uuid-here",
		OriginalFileName: "IMG_1234.jpg",
		OriginalPath:     "/upload/library/admin/2024/09/16/IMG_1234.jpg",
		FileCreatedAt:    created,
		Checksum:         "sha256",
		ExifInfo: &immichExifInfo{
			DateTimeOriginal: created,
			Make:             "Canon",
			Model:            "EOS R5",
			Latitude:         &lat,
			Longitude:        &lng,
			ImageWidth:       8192,
			ImageHeight:      5464,
		},
		People: []immichPersonRef{{ID: "p1", Name: "Mom"}, {ID: "p2", Name: "Dad"}},
	}
	entry := c.buildEntry(asset, []string{"Vacation", "Family"})

	if entry.Path != "/All Photos/2024/09/IMG_1234-abc12345.jpg" {
		t.Errorf("Path = %q, want /All Photos/2024/09/IMG_1234-abc12345.jpg", entry.Path)
	}
	if entry.Kind != "file" {
		t.Errorf("Kind = %q, want file", entry.Kind)
	}
	if entry.Extension != "jpg" {
		t.Errorf("Extension = %q, want jpg", entry.Extension)
	}
	if entry.ContentHash != "sha1:sha256" {
		t.Errorf("ContentHash = %q, want sha1:sha256 (immich connector now namespaces the checksum)", entry.ContentHash)
	}
	dm := entry.DomainMetadata
	if dm["immich_id"] != "abc12345-uuid-here" {
		t.Errorf("immich_id = %v, want abc12345-uuid-here", dm["immich_id"])
	}
	if dm["camera_make"] != "Canon" || dm["camera_model"] != "EOS R5" {
		t.Errorf("EXIF fields wrong: %+v", dm)
	}
	if dm["gps_lat"].(float64) != lat || dm["gps_lng"].(float64) != lng {
		t.Errorf("GPS not surfaced: %+v", dm)
	}
	albums, ok := dm["album"].([]string)
	if !ok || len(albums) != 2 {
		t.Errorf("album list missing: %+v", dm["album"])
	}
	people, ok := dm["person"].([]string)
	if !ok || len(people) != 2 || people[0] != "Mom" {
		t.Errorf("person list missing: %+v", dm["person"])
	}
}

func TestImmichBuildEntryNoExif(t *testing.T) {
	c := &ImmichConnector{}
	asset := immichAsset{
		ID:               "00000000-empty",
		OriginalFileName: "file.heic",
	}
	entry := c.buildEntry(asset, nil)
	if !strings.HasPrefix(entry.Path, "/All Photos/Undated/00/") {
		t.Errorf("expected /All Photos/Undated/00/ prefix, got %q", entry.Path)
	}
	dm := entry.DomainMetadata
	if dm["original_filename"] != "file.heic" {
		t.Errorf("original_filename missing: %+v", dm)
	}
	if _, ok := dm["album"]; ok {
		t.Errorf("album should be absent when no albums passed")
	}
}

// Smoke-test the full Walk against a fake Immich server. Tests:
//   - Connect probes /api/users/me (validates server + api key)
//   - Connect loads /api/albums + /api/albums/{id}
//   - Walk paginates /api/search/metadata
//   - assets-with-album mapping populates domain_metadata.album
//   - album_filter whitelisting drops non-matching assets
func TestImmichWalkAgainstFakeServer(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/users/me", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-api-key") != "secret" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		w.Write([]byte(`{"id":"u1","email":"a@b.c"}`))
	})
	mux.HandleFunc("/api/albums", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]any{
			{"id": "alb-1", "albumName": "Vacation"},
			{"id": "alb-2", "albumName": "Pets"},
		})
	})
	mux.HandleFunc("/api/albums/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/api/albums/")
		switch id {
		case "alb-1":
			json.NewEncoder(w).Encode(map[string]any{
				"id": "alb-1", "albumName": "Vacation",
				"assets": []map[string]any{{"id": "asset-A"}},
			})
		case "alb-2":
			json.NewEncoder(w).Encode(map[string]any{
				"id": "alb-2", "albumName": "Pets",
				"assets": []map[string]any{{"id": "asset-B"}},
			})
		default:
			http.Error(w, "not found", http.StatusNotFound)
		}
	})
	page := 0
	mux.HandleFunc("/api/search/metadata", func(w http.ResponseWriter, r *http.Request) {
		page++
		switch page {
		case 1:
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{
					"nextPage": "2",
					"items": []map[string]any{
						{
							"id":               "asset-A",
							"originalFileName": "vacation.jpg",
							"fileCreatedAt":    "2024-07-01T10:00:00Z",
							"checksum":         "hash-A",
						},
					},
				},
			})
		case 2:
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{
					"nextPage": "",
					"items": []map[string]any{
						{
							"id":               "asset-B",
							"originalFileName": "dog.jpg",
							"fileCreatedAt":    "2024-08-15T10:00:00Z",
							"checksum":         "hash-B",
						},
					},
				},
			})
		default:
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{"nextPage": "", "items": []any{}},
			})
		}
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	if got := c.assetAlbums["asset-A"]; len(got) != 1 || got[0] != "Vacation" {
		t.Errorf("assetAlbums[asset-A] = %v, want [Vacation]", got)
	}
	if got := c.assetAlbums["asset-B"]; len(got) != 1 || got[0] != "Pets" {
		t.Errorf("assetAlbums[asset-B] = %v, want [Pets]", got)
	}

	var entries []*models.EntryRecord
	_, err := c.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error {
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	// 2 files (asset-A in 2024/07, asset-B in 2024/08).
	// Directory rows: /All Photos, /All Photos/2024, /All Photos/2024/07,
	// /All Photos/2024/08 = 4 dirs (each ancestor emitted once across
	// the walk). Total: 6 entries.
	if len(entries) != 6 {
		t.Fatalf("entries = %d, want 6: %v", len(entries), entriesForLog(entries))
	}
	dirs, files := 0, 0
	for _, e := range entries {
		if e.Kind == "directory" {
			dirs++
		} else {
			files++
		}
	}
	if dirs != 4 || files != 2 {
		t.Errorf("dir/file = %d/%d, want 4/2", dirs, files)
	}

	// Album-filter test: only "Vacation".
	page = 0 // reset the fake's pagination counter
	c2 := NewImmichConnector(srv.URL, "secret", []string{"Vacation"}, false, true)
	if err := c2.Connect(context.Background()); err != nil {
		t.Fatalf("Connect 2: %v", err)
	}
	var filtered []*models.EntryRecord
	_, err = c2.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error {
		filtered = append(filtered, e)
		return nil
	})
	if err != nil {
		t.Fatalf("Walk filtered: %v", err)
	}
	// Only asset-A passes (in "Vacation"). Dirs: /All Photos, /All Photos/2024,
	// /All Photos/2024/07. Total: 3 dirs + 1 file = 4.
	if len(filtered) != 4 {
		t.Errorf("filtered entries = %d, want 4: %v", len(filtered), entriesForLog(filtered))
	}
}

func TestImmichAuthRejection(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no", http.StatusUnauthorized)
	}))
	defer srv.Close()
	c := NewImmichConnector(srv.URL, "wrong", nil, false, true)
	if err := c.Connect(context.Background()); err == nil {
		t.Fatalf("Connect with bad key should fail")
	} else if !strings.Contains(err.Error(), "auth rejected") {
		t.Errorf("error didn't mention auth rejection: %v", err)
	}
}

// v0.39.0 — A single transient Immich 5xx used to fail the entire scan
// after the walker had already paginated through tens of thousands of
// assets. fetchJSON now retries 5xx (and 408/429, and transport
// blips) with exponential backoff. Verify the probe survives two
// flakes before getting a 200, and verify retry exhaustion still
// surfaces the underlying error.
func TestImmichFetchJSONRetriesTransientServerErrors(t *testing.T) {
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		if hits < 3 {
			http.Error(w, "upstream hiccup", http.StatusBadGateway)
			return
		}
		w.Write([]byte(`{"id":"u1","email":"a@b.c"}`))
	}))
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	c.httpClient = &http.Client{Timeout: 2 * time.Second}
	if _, err := c.fetchJSON(context.Background(), http.MethodGet, "/api/users/me", nil); err != nil {
		t.Fatalf("fetchJSON should have succeeded after 2 retries, got: %v", err)
	}
	if hits != 3 {
		t.Errorf("expected 3 attempts (2 flakes + 1 success), got %d", hits)
	}
}

func TestImmichFetchJSONRetriesExhaust(t *testing.T) {
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		http.Error(w, "still broken", http.StatusServiceUnavailable)
	}))
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	c.httpClient = &http.Client{Timeout: 2 * time.Second}
	_, err := c.fetchJSON(context.Background(), http.MethodGet, "/api/users/me", nil)
	if err == nil {
		t.Fatalf("fetchJSON should have failed after exhausting retries")
	}
	if !strings.Contains(err.Error(), "4 attempts failed") {
		t.Errorf("error should mention attempt-exhaustion, got: %v", err)
	}
	if hits != immichMaxAttempts {
		t.Errorf("expected %d attempts, got %d", immichMaxAttempts, hits)
	}
}

// v0.40.0 — a single Immich 5xx on a specific page (per Immich
// #24359 — TOAST corruption on one asset row) must not fail the
// entire scan. Walk should skip just that page, increment
// UpstreamPagesSkipped, fire the warn hook once, and continue.
func TestImmichWalkSkipsSinglePoisonedPage(t *testing.T) {
	origBackoff := immichBackoffBase
	immichBackoffBase = 1 * time.Millisecond
	defer func() { immichBackoffBase = origBackoff }()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/users/me", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"id":"u1","email":"a@b.c"}`))
	})
	mux.HandleFunc("/api/albums", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`[]`))
	})
	mux.HandleFunc("/api/albums/", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no albums", http.StatusNotFound)
	})
	var pageSeq int
	mux.HandleFunc("/api/search/metadata", func(w http.ResponseWriter, r *http.Request) {
		pageSeq++
		// Page 1: ok. Pages 2..5 (the four retry attempts on page 2):
		// 500. Page 3: ok, last page.
		switch {
		case pageSeq == 1:
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{"nextPage": "2", "items": []map[string]any{
					{"id": "asset-A", "originalFileName": "a.jpg",
						"fileCreatedAt": "2024-01-01T00:00:00Z", "checksum": "h-A"},
				}},
			})
		case pageSeq >= 2 && pageSeq <= 5:
			http.Error(w,
				`{"message":"Failed to search assets","correlationId":"abc123"}`,
				http.StatusInternalServerError)
		case pageSeq == 6:
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{"nextPage": "", "items": []map[string]any{
					{"id": "asset-C", "originalFileName": "c.jpg",
						"fileCreatedAt": "2024-03-01T00:00:00Z", "checksum": "h-C"},
				}},
			})
		default:
			t.Fatalf("unexpected sequence number %d", pageSeq)
		}
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	var warns []string
	c.SetWarnHook(func(format string, args ...any) {
		warns = append(warns, fmt.Sprintf(format, args...))
	})
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}

	var emitted []*models.EntryRecord
	stats, err := c.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error {
		emitted = append(emitted, e)
		return nil
	})
	if err != nil {
		t.Fatalf("Walk should have succeeded despite the bad page, got: %v", err)
	}
	files := 0
	for _, e := range emitted {
		if e.Kind == "file" {
			files++
		}
	}
	if files != 2 {
		t.Errorf("emitted file count = %d, want 2 (asset-A + asset-C); skipped page should not block sibling pages", files)
	}
	if stats.UpstreamPagesSkipped != 1 {
		t.Errorf("UpstreamPagesSkipped = %d, want 1", stats.UpstreamPagesSkipped)
	}
	if len(warns) != 1 {
		t.Fatalf("warn hook fired %d times, want 1: %v", len(warns), warns)
	}
	if !strings.Contains(warns[0], "page 2") || !strings.Contains(warns[0], "correlationId") {
		t.Errorf("warn should name the page number and include the upstream snippet (with correlationId), got: %q", warns[0])
	}
}

// Three consecutive page failures = Immich is genuinely down (not
// per-row corruption); abort rather than silently lose hundreds of
// assets. Verify Walk returns the underlying error.
func TestImmichWalkAbortsAfterConsecutivePageFailures(t *testing.T) {
	origBackoff := immichBackoffBase
	immichBackoffBase = 1 * time.Millisecond
	defer func() { immichBackoffBase = origBackoff }()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/users/me", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"id":"u1"}`))
	})
	mux.HandleFunc("/api/albums", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`[]`))
	})
	mux.HandleFunc("/api/albums/", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no", http.StatusNotFound)
	})
	var pageSeq int
	mux.HandleFunc("/api/search/metadata", func(w http.ResponseWriter, r *http.Request) {
		pageSeq++
		if pageSeq == 1 {
			json.NewEncoder(w).Encode(map[string]any{
				"assets": map[string]any{"nextPage": "2", "items": []map[string]any{
					{"id": "asset-A", "originalFileName": "a.jpg",
						"fileCreatedAt": "2024-01-01T00:00:00Z"},
				}},
			})
			return
		}
		http.Error(w, "still broken", http.StatusInternalServerError)
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	_, err := c.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error { return nil })
	if err == nil {
		t.Fatalf("Walk should fail after %d consecutive page failures", immichMaxConsecutivePageFailures)
	}
	if !strings.Contains(err.Error(), "consecutive page failures") {
		t.Errorf("error should mention consecutive failures, got: %v", err)
	}
}

// Page 1 failing is Immich genuinely unavailable, not a poisoned
// row. The skip-page heuristic only kicks in for page > 1; page 1
// 5xx after retries stays fatal.
func TestImmichWalkFirstPage5xxStillFatal(t *testing.T) {
	origBackoff := immichBackoffBase
	immichBackoffBase = 1 * time.Millisecond
	defer func() { immichBackoffBase = origBackoff }()

	mux := http.NewServeMux()
	mux.HandleFunc("/api/users/me", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"id":"u1"}`))
	})
	mux.HandleFunc("/api/albums", func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`[]`))
	})
	mux.HandleFunc("/api/albums/", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no", http.StatusNotFound)
	})
	mux.HandleFunc("/api/search/metadata", func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "broken from the start", http.StatusInternalServerError)
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "secret", nil, false, true)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	stats, err := c.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error { return nil })
	if err == nil {
		t.Fatalf("Walk should fail on first-page 5xx, not skip-and-continue")
	}
	if !strings.Contains(err.Error(), "4 attempts failed") {
		t.Errorf("error should be retry-exhausted, got: %v", err)
	}
	if stats.UpstreamPagesSkipped != 0 {
		t.Errorf("UpstreamPagesSkipped = %d, want 0 (first-page failure must not be skipped)", stats.UpstreamPagesSkipped)
	}
}

// A 4xx (other than 408/429) must not be retried — those are misuse
// signals, not transient. Verify the auth path (401) still terminates
// after a single attempt rather than burning the retry budget.
func TestImmichFetchJSONDoesNotRetryAuthRejection(t *testing.T) {
	var hits int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits++
		http.Error(w, "no", http.StatusUnauthorized)
	}))
	defer srv.Close()

	c := NewImmichConnector(srv.URL, "wrong", nil, false, true)
	c.httpClient = &http.Client{Timeout: 2 * time.Second}
	if _, err := c.fetchJSON(context.Background(), http.MethodGet, "/api/users/me", nil); err == nil {
		t.Fatalf("fetchJSON should have failed on 401")
	}
	if hits != 1 {
		t.Errorf("401 should be terminal (no retry), got %d attempts", hits)
	}
}
