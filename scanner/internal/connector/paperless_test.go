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

func TestPathBaseAndAncestors(t *testing.T) {
	if got := pathBase("/IRS/Tax forms/2024"); got != "2024" {
		t.Errorf("pathBase(/IRS/Tax forms/2024) = %q, want 2024", got)
	}
	got := ancestorPaths("/IRS/Tax forms/2024/W-2.pdf")
	want := []string{"/IRS", "/IRS/Tax forms", "/IRS/Tax forms/2024"}
	if len(got) != len(want) {
		t.Fatalf("ancestorPaths len = %d, want %d (%v)", len(got), len(want), got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("ancestorPaths[%d] = %q, want %q", i, got[i], want[i])
		}
	}
	if got := ancestorPaths("/oneonly.pdf"); len(got) != 0 {
		t.Errorf("ancestorPaths(single segment) = %v, want []", got)
	}
}

func TestSafeSegment(t *testing.T) {
	cases := map[string]string{
		"":                 "_",
		"   ":              "_",
		"plain":            "plain",
		"with space":       "with space",
		"slash/in/it":      "slash_in_it",
		"back\\slash":      "back_slash",
		"control\x00null":  "control_null",
		"unicode-naïve":    "unicode-naïve",
		"  trim me  ":      "trim me",
	}
	for in, want := range cases {
		if got := safeSegment(in); got != want {
			t.Errorf("safeSegment(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestNextPath(t *testing.T) {
	if got := nextPath("", "http://example.com"); got != "" {
		t.Errorf("nextPath('') = %q, want ''", got)
	}
	got := nextPath("https://paperless.example.com/api/documents/?page=2&page_size=100", "https://paperless.example.com")
	want := "/api/documents/?page=2&page_size=100"
	if got != want {
		t.Errorf("nextPath = %q, want %q", got, want)
	}
}

func TestDocExtension(t *testing.T) {
	cases := []struct {
		doc  paperlessDocument
		want string
	}{
		{paperlessDocument{OriginalFileName: "tax-2024.PDF"}, "pdf"},
		{paperlessDocument{OriginalFileName: "scan.tiff"}, "tiff"},
		{paperlessDocument{OriginalFileName: "no-ext"}, "pdf"},
		{paperlessDocument{}, "pdf"},
	}
	for _, c := range cases {
		if got := docExtension(c.doc); got != c.want {
			t.Errorf("docExtension(%+v) = %q, want %q", c.doc, got, c.want)
		}
	}
}

func TestBuildEntry(t *testing.T) {
	c := &PaperlessConnector{
		correspondents: map[int]string{1: "IRS"},
		documentTypes:  map[int]string{2: "Tax form"},
		tags:           map[int]string{10: "tax", 11: "2024"},
	}
	corrID, dtID := 1, 2
	created := time.Date(2024, 4, 14, 10, 0, 0, 0, time.UTC)
	mod := time.Date(2024, 4, 15, 10, 0, 0, 0, time.UTC)
	doc := paperlessDocument{
		ID:                  42,
		Title:               "W-2",
		Tags:                []int{10, 11},
		Correspondent:       &corrID,
		DocumentType:        &dtID,
		Created:             created,
		Modified:            &mod,
		OriginalFileName:    "w2-2024.pdf",
		ContentChecksum:     "abc123",
		Content:             "Wages, tips, other compensation: $50,000",
	}
	entry := c.buildEntry(doc, c.resolveTags(doc.Tags))
	if entry.Path != "/IRS/Tax form/2024/W-2.pdf" {
		t.Errorf("Path = %q, want /IRS/Tax form/2024/W-2.pdf", entry.Path)
	}
	if entry.Name != "W-2.pdf" {
		t.Errorf("Name = %q, want W-2.pdf", entry.Name)
	}
	if entry.Kind != "file" {
		t.Errorf("Kind = %q, want file", entry.Kind)
	}
	if entry.Extension != "pdf" {
		t.Errorf("Extension = %q, want pdf", entry.Extension)
	}
	if entry.ModifiedAt == nil || !entry.ModifiedAt.Equal(mod) {
		t.Errorf("ModifiedAt = %v, want %v", entry.ModifiedAt, mod)
	}
	if entry.ContentHash != "abc123" {
		t.Errorf("ContentHash = %q, want abc123", entry.ContentHash)
	}
	if entry.DomainMetadata == nil {
		t.Fatalf("DomainMetadata is nil")
	}
	dm := entry.DomainMetadata
	if dm["correspondent"] != "IRS" || dm["document_type"] != "Tax form" {
		t.Errorf("metadata correspondent/document_type wrong: %+v", dm)
	}
	if id, ok := dm["paperless_id"].(int); !ok || id != 42 {
		t.Errorf("paperless_id = %v, want 42", dm["paperless_id"])
	}
	tagsList, ok := dm["tags"].([]string)
	if !ok || len(tagsList) != 2 {
		t.Fatalf("tags = %v, want [tax 2024]", dm["tags"])
	}
	if dm["title"] != "W-2" {
		t.Errorf("title = %v, want W-2", dm["title"])
	}
	preview, _ := dm["content_preview"].(string)
	if !strings.HasPrefix(preview, "Wages") {
		t.Errorf("content_preview missing Wages prefix: %q", preview)
	}
}

func TestBuildEntryFallbacks(t *testing.T) {
	c := &PaperlessConnector{
		correspondents: map[int]string{},
		documentTypes:  map[int]string{},
		tags:           map[int]string{},
	}
	doc := paperlessDocument{ID: 7, Title: ""}
	entry := c.buildEntry(doc, nil)
	if !strings.HasPrefix(entry.Path, "/Unsorted/Unfiled/Undated/") {
		t.Errorf("expected /Unsorted/Unfiled/Undated/* path, got %q", entry.Path)
	}
	if !strings.Contains(entry.Path, "document-7") {
		t.Errorf("blank title should fall back to document-<id>, got %q", entry.Path)
	}
}

// Smoke-test the full Walk flow against a fake Paperless server.
// Connect() loads three lookup tables and then Walk() paginates the
// documents endpoint. Confirms the connector emits one directory row
// per ancestor + one file row per document, follows pagination, and
// honours tag_filter.
func TestPaperlessWalkAgainstFakeServer(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/correspondents/", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"count":   1,
			"next":    "",
			"results": []map[string]any{{"id": 1, "name": "IRS"}},
		})
	})
	mux.HandleFunc("/api/document_types/", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"count":   1,
			"next":    "",
			"results": []map[string]any{{"id": 2, "name": "Tax form"}},
		})
	})
	mux.HandleFunc("/api/tags/", func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{
			"count": 2,
			"next":  "",
			"results": []map[string]any{
				{"id": 10, "name": "tax"},
				{"id": 11, "name": "personal"},
			},
		})
	})
	mux.HandleFunc("/api/documents/", func(w http.ResponseWriter, r *http.Request) {
		// Auth header check: token "secret".
		if got := r.Header.Get("Authorization"); got != "Token secret" {
			http.Error(w, "bad auth", http.StatusUnauthorized)
			return
		}
		page := r.URL.Query().Get("page")
		switch page {
		case "", "1":
			json.NewEncoder(w).Encode(map[string]any{
				"count": 2,
				"next":  "http://" + r.Host + "/api/documents/?page=2&page_size=100",
				"results": []map[string]any{
					{
						"id":            42,
						"title":         "W-2",
						"correspondent": 1,
						"document_type": 2,
						"tags":          []int{10},
						"created":       "2024-04-14T10:00:00Z",
					},
				},
			})
		case "2":
			json.NewEncoder(w).Encode(map[string]any{
				"count": 2,
				"next":  "",
				"results": []map[string]any{
					{
						"id":            43,
						"title":         "Receipt",
						"correspondent": 1,
						"document_type": 2,
						"tags":          []int{11},
						"created":       "2024-04-14T11:00:00Z",
					},
				},
			})
		}
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewPaperlessConnector(srv.URL, "secret", nil, true)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}

	var entries []*models.EntryRecord
	_, err := c.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error {
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	// Two documents → 1 file each, plus 3 unique parent dirs:
	// /IRS, /IRS/Tax form, /IRS/Tax form/2024 — total 5.
	if len(entries) != 5 {
		t.Fatalf("entries = %d, want 5; got %v", len(entries), entriesForLog(entries))
	}
	dirs, files := 0, 0
	for _, e := range entries {
		if e.Kind == "directory" {
			dirs++
		} else {
			files++
		}
	}
	if dirs != 3 || files != 2 {
		t.Errorf("dir/file split = %d/%d, want 3/2", dirs, files)
	}

	// Tag filter: only 'tax' tagged documents.
	c2 := NewPaperlessConnector(srv.URL, "secret", []string{"tax"}, true)
	if err := c2.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	var filtered []*models.EntryRecord
	_, err = c2.Walk(context.Background(), "/", nil, false, false, func(e *models.EntryRecord) error {
		filtered = append(filtered, e)
		return nil
	})
	if err != nil {
		t.Fatalf("Walk with filter: %v", err)
	}
	// Only W-2 (tagged "tax") passes; receipt (tagged "personal") is
	// dropped. Same 3 dirs + 1 file = 4 entries.
	if len(filtered) != 4 {
		t.Errorf("tag-filtered entries = %d, want 4: %v", len(filtered), entriesForLog(filtered))
	}
}

func TestPaperlessAuthRejection(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no", http.StatusUnauthorized)
	}))
	defer srv.Close()
	c := NewPaperlessConnector(srv.URL, "wrong", nil, true)
	if err := c.Connect(context.Background()); err == nil {
		t.Fatalf("Connect with bad token should fail")
	} else if !strings.Contains(err.Error(), "auth rejected") {
		t.Errorf("error didn't mention auth rejection: %v", err)
	}
}

func entriesForLog(entries []*models.EntryRecord) []string {
	out := make([]string, len(entries))
	for i, e := range entries {
		out[i] = e.Kind + ":" + e.Path
	}
	return out
}
