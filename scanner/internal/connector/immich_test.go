package connector

import (
	"context"
	"encoding/json"
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
//   - Connect probes /api/server-info/ping
//   - Connect loads /api/album + /api/album/{id}
//   - Walk paginates /api/search/metadata
//   - assets-with-album mapping populates domain_metadata.album
//   - album_filter whitelisting drops non-matching assets
func TestImmichWalkAgainstFakeServer(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/server-info/ping", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("x-api-key") != "secret" {
			http.Error(w, "no", http.StatusUnauthorized)
			return
		}
		w.Write([]byte(`{"res":"pong"}`))
	})
	mux.HandleFunc("/api/album", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode([]map[string]any{
			{"id": "alb-1", "albumName": "Vacation"},
			{"id": "alb-2", "albumName": "Pets"},
		})
	})
	mux.HandleFunc("/api/album/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/api/album/")
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
