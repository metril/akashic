package connector

import (
	"context"
	"encoding/xml"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// nextcloud-shaped multistatus body for the parser. Captures the
// quirks we care about: RFC1123 dates, percent-encoded hrefs,
// trailing-slash dirs vs no-slash files, ETag with quotes.
const sampleNextcloudResponse = `<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/admin/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Thu, 02 May 2024 12:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/admin/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Thu, 02 May 2024 12:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/admin/Photos%20and%20Videos/</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getlastmodified>Thu, 02 May 2024 12:00:00 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/admin/note.md</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>1024</d:getcontentlength>
        <d:getlastmodified>Thu, 02 May 2024 12:00:00 GMT</d:getlastmodified>
        <d:getcontenttype>text/markdown</d:getcontenttype>
        <d:getetag>"abc123"</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>`

func TestParseResponseDirectoryAndFile(t *testing.T) {
	c := &WebDAVConnector{basePath: "/remote.php/dav/files/admin/"}
	var ms multistatus
	if err := xml.Unmarshal([]byte(sampleNextcloudResponse), &ms); err != nil {
		t.Fatalf("xml unmarshal: %v", err)
	}
	if len(ms.Responses) != 4 {
		t.Fatalf("Responses = %d, want 4", len(ms.Responses))
	}
	// First response = the parent itself (rel = "").
	parent, ok := c.parseResponse(ms.Responses[0])
	if !ok {
		t.Fatal("parent response should parse")
	}
	if parent.relPath != "" {
		t.Errorf("parent relPath = %q, want \"\"", parent.relPath)
	}
	if !parent.isDir {
		t.Errorf("parent should be dir")
	}

	// Second = Documents subdir.
	doc, _ := c.parseResponse(ms.Responses[1])
	if doc.relPath != "Documents" || !doc.isDir {
		t.Errorf("Documents: relPath=%q isDir=%v, want Documents true", doc.relPath, doc.isDir)
	}

	// Third = Photos and Videos (percent-encoded space).
	photos, _ := c.parseResponse(ms.Responses[2])
	if photos.relPath != "Photos and Videos" || !photos.isDir {
		t.Errorf("photos: relPath=%q isDir=%v, want \"Photos and Videos\" true", photos.relPath, photos.isDir)
	}

	// Fourth = file with size + content-type + etag.
	file, _ := c.parseResponse(ms.Responses[3])
	if file.relPath != "note.md" {
		t.Errorf("file relPath = %q, want note.md", file.relPath)
	}
	if file.isDir {
		t.Errorf("file should not be dir")
	}
	if file.size != 1024 {
		t.Errorf("file size = %d, want 1024", file.size)
	}
	if file.contentType != "text/markdown" {
		t.Errorf("file contentType = %q", file.contentType)
	}
	if file.etag != "abc123" {
		t.Errorf("file etag = %q, want abc123 (quotes stripped)", file.etag)
	}
	expectedTime, _ := time.Parse(time.RFC1123, "Thu, 02 May 2024 12:00:00 GMT")
	if !file.modified.Equal(expectedTime) {
		t.Errorf("file modified = %v, want %v", file.modified, expectedTime)
	}
}

func TestBuildWebDAVEntryFile(t *testing.T) {
	mod := time.Date(2024, 5, 2, 12, 0, 0, 0, time.UTC)
	e := webdavEntry{
		relPath:     "Documents/report.pdf",
		isDir:       false,
		size:        2048,
		modified:    mod,
		contentType: "application/pdf",
		etag:        "v17",
	}
	entry := buildWebDAVEntry(e)
	if entry.Path != "/Documents/report.pdf" {
		t.Errorf("Path = %q, want /Documents/report.pdf", entry.Path)
	}
	if entry.Name != "report.pdf" {
		t.Errorf("Name = %q, want report.pdf", entry.Name)
	}
	if entry.Kind != "file" {
		t.Errorf("Kind = %q, want file", entry.Kind)
	}
	if entry.Extension != "pdf" {
		t.Errorf("Extension = %q, want pdf", entry.Extension)
	}
	if entry.MimeType != "application/pdf" {
		t.Errorf("MimeType = %q", entry.MimeType)
	}
	if entry.ContentHash != "etag:v17" {
		t.Errorf("ContentHash = %q, want etag:v17", entry.ContentHash)
	}
	if entry.SizeBytes == nil || *entry.SizeBytes != 2048 {
		t.Errorf("SizeBytes = %v, want 2048", entry.SizeBytes)
	}
	if !entry.ModifiedAt.Equal(mod) {
		t.Errorf("ModifiedAt = %v", entry.ModifiedAt)
	}
}

func TestBuildWebDAVEntryDirectory(t *testing.T) {
	e := webdavEntry{relPath: "Photos", isDir: true}
	entry := buildWebDAVEntry(e)
	if entry.Path != "/Photos" || entry.Kind != "directory" {
		t.Errorf("dir entry wrong: %+v", entry)
	}
	if entry.SizeBytes != nil {
		t.Errorf("dir should have no size")
	}
}

// Smoke-test the BFS Walk against a fake WebDAV server. Two-deep
// hierarchy: root → Documents → file.pdf, plus a top-level photo.
func TestWebDAVWalkAgainstFakeServer(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "PROPFIND" {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		if got := r.Header.Get("Authorization"); !strings.HasPrefix(got, "Basic ") {
			http.Error(w, "no auth", http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/xml; charset=utf-8")
		w.WriteHeader(http.StatusMultiStatus)
		switch r.URL.Path {
		case "/dav/", "/dav":
			fmt.Fprint(w, `<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">`+
				`<d:response><d:href>/dav/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>`+
				`<d:response><d:href>/dav/Documents/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>`+
				`<d:response><d:href>/dav/photo.jpg</d:href><d:propstat><d:prop><d:resourcetype/><d:getcontentlength>500</d:getcontentlength><d:getcontenttype>image/jpeg</d:getcontenttype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>`+
				`</d:multistatus>`)
		case "/dav/Documents/", "/dav/Documents":
			fmt.Fprint(w, `<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">`+
				`<d:response><d:href>/dav/Documents/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>`+
				`<d:response><d:href>/dav/Documents/file.pdf</d:href><d:propstat><d:prop><d:resourcetype/><d:getcontentlength>1000</d:getcontentlength><d:getcontenttype>application/pdf</d:getcontenttype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>`+
				`</d:multistatus>`)
		}
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewWebDAVConnector(srv.URL+"/dav/", "user", "pass", true)
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
	// Root yields: Documents (dir), photo.jpg (file).
	// Documents yields: file.pdf.
	// Total: 2 dir + 2 file = 4 entries (Documents+photo+file.pdf,
	// plus the Documents dir is emitted once). Wait: PROPFIND on
	// root returns parent + 2 children; we skip the parent. So
	// from root we emit Documents + photo. From Documents we
	// emit file.pdf. Total = 3 entries.
	if len(entries) != 3 {
		t.Fatalf("entries = %d, want 3: %v", len(entries), entriesForLog(entries))
	}
	dirs, files := 0, 0
	for _, e := range entries {
		if e.Kind == "directory" {
			dirs++
		} else {
			files++
		}
	}
	if dirs != 1 || files != 2 {
		t.Errorf("dir/file = %d/%d, want 1/2", dirs, files)
	}
}

func TestWebDAVAuthRejection(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no", http.StatusUnauthorized)
	}))
	defer srv.Close()
	c := NewWebDAVConnector(srv.URL+"/", "user", "wrong", true)
	if err := c.Connect(context.Background()); err == nil {
		t.Fatalf("Connect with bad auth should fail")
	} else if !strings.Contains(err.Error(), "auth rejected") {
		t.Errorf("error didn't mention auth rejection: %v", err)
	}
}

func TestWebDAVMethodNotAllowed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "no propfind here", http.StatusMethodNotAllowed)
	}))
	defer srv.Close()
	c := NewWebDAVConnector(srv.URL+"/", "user", "pass", true)
	if err := c.Connect(context.Background()); err == nil {
		t.Fatalf("Connect should fail when server doesn't speak WebDAV")
	} else if !strings.Contains(err.Error(), "PROPFIND not allowed") {
		t.Errorf("error didn't mention PROPFIND: %v", err)
	}
}
