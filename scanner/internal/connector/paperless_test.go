package connector

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
	"github.com/akashic-project/akashic/scanner/internal/wire"
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
		"":                "_",
		"   ":             "_",
		"plain":           "plain",
		"with space":      "with space",
		"slash/in/it":     "slash_in_it",
		"back\\slash":     "back_slash",
		"control\x00null": "control_null",
		"unicode-naïve":   "unicode-naïve",
		"  trim me  ":     "trim me",
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

const testSum = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"

func TestBuildEntryNoArchive(t *testing.T) {
	c := &PaperlessConnector{}
	size := int64(5)
	meta := paperlessMetadata{MediaFilename: "0000001.pdf", OriginalChecksum: testSum, OriginalSize: &size}
	entry := c.buildEntry(paperlessDocument{ID: 1, Title: "x"}, nil, meta, nil)
	dm := wire.Deref(entry.DomainMetadata)
	if dm["media_filename"] != "0000001.pdf" {
		t.Errorf("media_filename = %v", dm["media_filename"])
	}
	for _, k := range []string{"archive_media_filename", "archive_checksum", "archive_size", "versions"} {
		if _, ok := dm[k]; ok {
			t.Errorf("key %q should be absent", k)
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
		ID:               42,
		Title:            "W-2",
		Tags:             []int{10, 11},
		Correspondent:    &corrID,
		DocumentType:     &dtID,
		Created:          created,
		Modified:         &mod,
		OriginalFileName: "w2-2024.pdf",
		Content:          "Wages, tips, other compensation: $50,000",
	}
	size := int64(12345)
	arc := "0000042.pdf"
	arcSum := "ffee"
	arcSize := int64(999)
	meta := paperlessMetadata{
		MediaFilename:        "0000042.pdf",
		OriginalChecksum:     strings.ToUpper(testSum),
		OriginalSize:         &size,
		HasArchiveVersion:    true,
		ArchiveMediaFilename: &arc,
		ArchiveChecksum:      &arcSum,
		ArchiveSize:          &arcSize,
	}
	entry := c.buildEntry(doc, c.resolveTags(doc.Tags), meta, nil)
	if entry.Path != "/IRS/Tax form/2024/W-2.pdf" {
		t.Errorf("Path = %q, want /IRS/Tax form/2024/W-2.pdf", entry.Path)
	}
	if entry.Name != "W-2.pdf" {
		t.Errorf("Name = %q, want W-2.pdf", entry.Name)
	}
	if wire.Deref(entry.Kind) != apiclient.File {
		t.Errorf("Kind = %q, want file", wire.Deref(entry.Kind))
	}
	if wire.Deref(entry.Extension) != "pdf" {
		t.Errorf("Extension = %q, want pdf", wire.Deref(entry.Extension))
	}
	if entry.FsModifiedAt == nil || !entry.FsModifiedAt.Equal(mod) {
		t.Errorf("ModifiedAt = %v, want %v", entry.FsModifiedAt, mod)
	}
	if wire.Deref(entry.ContentHash) != "sha256:"+testSum {
		t.Errorf("ContentHash = %q, want sha256:%s", wire.Deref(entry.ContentHash), testSum)
	}
	if entry.SizeBytes == nil || *entry.SizeBytes != 12345 {
		t.Errorf("SizeBytes = %v, want 12345", entry.SizeBytes)
	}
	if entry.DomainMetadata == nil {
		t.Fatalf("DomainMetadata is nil")
	}
	dm := wire.Deref(entry.DomainMetadata)
	if dm["media_filename"] != "0000042.pdf" || dm["archive_media_filename"] != "0000042.pdf" ||
		dm["archive_checksum"] != "ffee" || dm["archive_size"] != int64(999) {
		t.Errorf("media/archive keys wrong: %+v", dm)
	}
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
	entry := c.buildEntry(doc, nil, paperlessMetadata{}, nil)
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
		if strings.HasSuffix(r.URL.Path, "/metadata/") {
			if r.Header.Get("Authorization") != "Token secret" {
				http.Error(w, "bad auth", http.StatusUnauthorized)
				return
			}
			json.NewEncoder(w).Encode(map[string]any{"original_checksum": testSum, "original_size": 1, "media_filename": "x.pdf"})
			return
		}
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

	var entries []*apiclient.EntryIn
	_, err := c.Walk(context.Background(), "/", nil, false, false, func(e *apiclient.EntryIn) error {
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
		if wire.Deref(e.Kind) == apiclient.Directory {
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
	var filtered []*apiclient.EntryIn
	_, err = c2.Walk(context.Background(), "/", nil, false, false, func(e *apiclient.EntryIn) error {
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

func entriesForLog(entries []*apiclient.EntryIn) []string {
	out := make([]string, len(entries))
	for i, e := range entries {
		out[i] = string(wire.Deref(e.Kind)) + ":" + e.Path
	}
	return out
}

// paperlessFake serves lookups, a one-page document list and per-doc
// metadata. metaHits counts metadata requests.
type fakeCfg struct {
	checksum string       // original_checksum override (default upper-case testSum)
	missing  map[int]bool // ids whose metadata 404s
}

func paperlessFake(t *testing.T, docs []map[string]any, metaStatus int, metaHits *int32, opts ...func(*fakeCfg)) *httptest.Server {
	t.Helper()
	cfg := &fakeCfg{checksum: strings.ToUpper(testSum), missing: map[int]bool{}}
	for _, o := range opts {
		o(cfg)
	}
	mux := http.NewServeMux()
	empty := func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"count": 0, "next": "", "results": []any{}})
	}
	mux.HandleFunc("/api/correspondents/", empty)
	mux.HandleFunc("/api/document_types/", empty)
	mux.HandleFunc("/api/tags/", empty)
	mux.HandleFunc("/api/documents/", func(w http.ResponseWriter, r *http.Request) {
		p := strings.TrimPrefix(r.URL.Path, "/api/documents/")
		if strings.HasSuffix(p, "/metadata/") {
			atomic.AddInt32(metaHits, 1)
			if metaStatus != 0 {
				http.Error(w, "boom", metaStatus)
				return
			}
			id := strings.TrimSuffix(p, "/metadata/")
			n, _ := strconv.Atoi(id)
			if cfg.missing[n] {
				http.NotFound(w, r)
				return
			}
			json.NewEncoder(w).Encode(map[string]any{
				"original_checksum":      cfg.checksum,
				"original_size":          1000 + n,
				"media_filename":         fmt.Sprintf("%07d.pdf", n),
				"has_archive_version":    true,
				"archive_media_filename": fmt.Sprintf("%07d.pdf", n),
				"archive_checksum":       "aa",
				"archive_size":           77,
			})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"count": len(docs), "next": "", "results": docs})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func walkPaperless(t *testing.T, url string) ([]*apiclient.EntryIn, error) {
	t.Helper()
	c := NewPaperlessConnector(url, "secret", nil, true)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	var files []*apiclient.EntryIn
	_, err := c.Walk(context.Background(), "/", nil, false, false, func(e *apiclient.EntryIn) error {
		if wire.Deref(e.Kind) == apiclient.File {
			files = append(files, e)
		}
		return nil
	})
	return files, err
}

func fakeDocs(n int, base int, mod string) []map[string]any {
	out := []map[string]any{}
	for i := 0; i < n; i++ {
		out = append(out, map[string]any{
			"id": base + i, "title": fmt.Sprintf("doc%d", base+i),
			"created": "2024-04-14T10:00:00Z", "modified": mod,
			"root_document": nil,
			"versions":      []map[string]any{{"id": base + i, "checksum": "x", "is_root": true}},
		})
	}
	return out
}

func TestPaperlessWalkFetchesMetadata(t *testing.T) {
	var hits int32
	srv := paperlessFake(t, fakeDocs(9, 100, "2024-05-01T00:00:00Z"), 0, &hits)
	files, err := walkPaperless(t, srv.URL)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(files) != 9 {
		t.Fatalf("files = %d, want 9", len(files))
	}
	for i, e := range files {
		dm := wire.Deref(e.DomainMetadata)
		if dm["paperless_id"] != 100+i {
			t.Errorf("order: entry %d has id %v", i, dm["paperless_id"])
		}
		if wire.Deref(e.ContentHash) != "sha256:"+testSum {
			t.Errorf("hash = %q", wire.Deref(e.ContentHash))
		}
		if e.SizeBytes == nil || *e.SizeBytes != 1100+i {
			t.Errorf("size = %v, want %d", e.SizeBytes, 1100+i)
		}
	}
}

func TestPaperlessWalkSkipsNonRootVersions(t *testing.T) {
	docs := []map[string]any{
		{
			"id": 219, "title": "root", "created": "2024-04-14T10:00:00Z", "modified": "2024-05-02T00:00:00Z",
			"root_document": nil,
			"versions": []map[string]any{
				{"id": 219, "checksum": "a", "is_root": true},
				{"id": 300, "checksum": "b", "is_root": false},
			},
		},
		{
			"id": 300, "title": "root", "created": "2024-04-14T10:00:00Z", "modified": "2024-05-02T00:00:00Z",
			"root_document": 219, "versions": []any{},
		},
	}
	var hits int32
	srv := paperlessFake(t, docs, 0, &hits)
	files, err := walkPaperless(t, srv.URL)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(files) != 1 {
		t.Fatalf("files = %d, want 1 (version doc skipped)", len(files))
	}
	if got := atomic.LoadInt32(&hits); got != 2 {
		t.Errorf("metadata fetches = %d, want 2 (root + version only)", got)
	}
	dm := wire.Deref(files[0].DomainMetadata)
	vs, ok := dm["versions"].([]map[string]any)
	if !ok || len(vs) != 1 {
		t.Fatalf("versions = %v", dm["versions"])
	}
	if vs[0]["media_filename"] != "0000300.pdf" || vs[0]["id"] != 300 {
		t.Errorf("version = %v", vs[0])
	}
}

func TestPaperlessMetadataCacheSkipsUnchanged(t *testing.T) {
	var hits int32
	// unique base so the process-wide cache is not shared with other tests
	srv := paperlessFake(t, fakeDocs(5, 5000, "2024-05-03T00:00:00Z"), 0, &hits)
	if _, err := walkPaperless(t, srv.URL); err != nil {
		t.Fatal(err)
	}
	if _, err := walkPaperless(t, srv.URL); err != nil {
		t.Fatal(err)
	}
	if got := atomic.LoadInt32(&hits); got != 5 {
		t.Errorf("metadata fetches = %d, want 5 (second walk cached)", got)
	}
}

func TestPaperlessMetadataErrorFailsWalk(t *testing.T) {
	var hits int32
	srv := paperlessFake(t, fakeDocs(3, 7000, "2024-05-04T00:00:00Z"), http.StatusInternalServerError, &hits)
	if _, err := walkPaperless(t, srv.URL); err == nil {
		t.Fatal("Walk should fail when metadata fetch errors")
	}
}

func TestPaperlessContentHashLabelling(t *testing.T) {
	md5sum := strings.Repeat("ab", 16)
	cases := []struct {
		name, sum, want string
	}{
		{"sha256", strings.ToUpper(testSum), "sha256:" + testSum},
		{"md5", strings.ToUpper(md5sum), "md5:" + md5sum},
		{"bad length", "abc123", ""},
		{"non-hex", strings.Repeat("z", 64), ""},
	}
	for i, tc := range cases {
		var hits int32
		sum := tc.sum
		srv := paperlessFake(t, fakeDocs(1, 8000+i, "2024-05-05T00:00:00Z"), 0, &hits, func(c *fakeCfg) { c.checksum = sum })
		files, err := walkPaperless(t, srv.URL)
		if err != nil || len(files) != 1 {
			t.Fatalf("%s: err=%v files=%d", tc.name, err, len(files))
		}
		if got := wire.Deref(files[0].ContentHash); got != tc.want {
			t.Errorf("%s: ContentHash = %q, want %q", tc.name, got, tc.want)
		}
	}
}

func TestPaperlessDeletedDocSkipped(t *testing.T) {
	var hits int32
	docs := fakeDocs(3, 9000, "2024-05-06T00:00:00Z")
	srv := paperlessFake(t, docs, 0, &hits, func(c *fakeCfg) { c.missing[9001] = true })
	files, err := walkPaperless(t, srv.URL)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(files) != 2 {
		t.Fatalf("files = %d, want 2 (404 doc skipped)", len(files))
	}
}

func TestPaperlessMissingVersionSkipped(t *testing.T) {
	docs := []map[string]any{{
		"id": 9100, "title": "r", "created": "2024-04-14T10:00:00Z", "modified": "2024-05-07T00:00:00Z",
		"root_document": nil,
		"versions": []map[string]any{
			{"id": 9100, "checksum": "a", "is_root": true},
			{"id": 9101, "checksum": "b", "is_root": false},
			{"id": 9102, "checksum": "c", "is_root": false},
		},
	}}
	var hits int32
	srv := paperlessFake(t, docs, 0, &hits, func(c *fakeCfg) { c.missing[9101] = true })
	files, err := walkPaperless(t, srv.URL)
	if err != nil || len(files) != 1 {
		t.Fatalf("err=%v files=%d", err, len(files))
	}
	vs := wire.Deref(files[0].DomainMetadata)["versions"].([]map[string]any)
	if len(vs) != 1 || vs[0]["id"] != 9102 {
		t.Errorf("versions = %v, want only 9102", vs)
	}
}

func TestPaperlessVersionMetadataCached(t *testing.T) {
	docs := []map[string]any{{
		"id": 9200, "title": "r", "created": "2024-04-14T10:00:00Z", "modified": "2024-05-08T00:00:00Z",
		"root_document": nil,
		"versions": []map[string]any{
			{"id": 9200, "checksum": "a", "is_root": true},
			{"id": 9201, "checksum": "b", "is_root": false},
		},
	}}
	var hits int32
	srv := paperlessFake(t, docs, 0, &hits)
	for i := 0; i < 2; i++ {
		if _, err := walkPaperless(t, srv.URL); err != nil {
			t.Fatal(err)
		}
	}
	if got := atomic.LoadInt32(&hits); got != 2 {
		t.Errorf("fetches = %d, want 2 (root+version once)", got)
	}
	// version checksum change invalidates only the version entry
	docs[0]["versions"].([]map[string]any)[1]["checksum"] = "changed"
	if _, err := walkPaperless(t, srv.URL); err != nil {
		t.Fatal(err)
	}
	if got := atomic.LoadInt32(&hits); got != 3 {
		t.Errorf("fetches = %d, want 3 after version checksum change", got)
	}
}

func TestPaperlessCacheEvictsUnseen(t *testing.T) {
	var cur atomic.Value
	cur.Store(fakeDocs(3, 9400, "2024-05-09T00:00:00Z"))
	mux := http.NewServeMux()
	empty := func(w http.ResponseWriter, r *http.Request) {
		json.NewEncoder(w).Encode(map[string]any{"count": 0, "next": "", "results": []any{}})
	}
	mux.HandleFunc("/api/correspondents/", empty)
	mux.HandleFunc("/api/document_types/", empty)
	mux.HandleFunc("/api/tags/", empty)
	mux.HandleFunc("/api/documents/", func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/metadata/") {
			json.NewEncoder(w).Encode(map[string]any{"original_checksum": testSum, "media_filename": "m.pdf"})
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"count": 0, "next": "", "results": cur.Load()})
	})
	srv3 := httptest.NewServer(mux)
	defer srv3.Close()
	if _, err := walkPaperless(t, srv3.URL); err != nil {
		t.Fatal(err)
	}
	count := func() int {
		paperlessMetaCache.Lock()
		defer paperlessMetaCache.Unlock()
		n := 0
		for k := range paperlessMetaCache.m {
			if strings.HasPrefix(k, srv3.URL+"|") {
				n++
			}
		}
		return n
	}
	if count() != 3 {
		t.Fatalf("cache = %d, want 3", count())
	}
	cur.Store(fakeDocs(1, 9400, "2024-05-09T00:00:00Z"))
	if _, err := walkPaperless(t, srv3.URL); err != nil {
		t.Fatal(err)
	}
	if count() != 1 {
		t.Errorf("cache = %d after docs removed, want 1", count())
	}
}
