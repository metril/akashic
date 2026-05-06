package connector

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fakeDropboxServer mocks /2/users/get_current_account, /2/files/list_folder,
// and /2/files/list_folder/continue with deterministic responses.
type fakeDropboxServer struct {
	*httptest.Server
	pages []dropboxListPage // returned in order; cursor advances per call
	calls int
}

func newFakeDropboxServer(t *testing.T, pages []dropboxListPage) *fakeDropboxServer {
	t.Helper()
	fs := &fakeDropboxServer{pages: pages}
	mux := http.NewServeMux()
	mux.HandleFunc("/2/users/get_current_account", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			http.Error(w, "auth", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"email": "alice@example.com",
			"name":  map[string]string{"display_name": "Alice"},
		})
	})
	mux.HandleFunc("/2/files/list_folder", func(w http.ResponseWriter, r *http.Request) {
		fs.serveListFolder(w, r)
	})
	mux.HandleFunc("/2/files/list_folder/continue", func(w http.ResponseWriter, r *http.Request) {
		fs.serveListFolder(w, r)
	})
	fs.Server = httptest.NewServer(mux)
	return fs
}

func (fs *fakeDropboxServer) serveListFolder(w http.ResponseWriter, r *http.Request) {
	if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		http.Error(w, "auth", http.StatusUnauthorized)
		return
	}
	if fs.calls >= len(fs.pages) {
		http.Error(w, "out of pages", 400)
		return
	}
	page := fs.pages[fs.calls]
	fs.calls++
	_ = json.NewEncoder(w).Encode(page)
}

func newDropboxTestConnector(srv *fakeDropboxServer) *DropboxConnector {
	c := NewDropboxConnector(&DropboxConfig{AccessToken: "test-token"})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteDropboxTransport{
			base:    transport,
			fakeURL: srv.URL,
		},
	}
	return c
}

type rewriteDropboxTransport struct {
	base    http.RoundTripper
	fakeURL string
}

func (r rewriteDropboxTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	for _, host := range []string{
		"https://api.dropboxapi.com",
		"https://content.dropboxapi.com",
	} {
		if strings.HasPrefix(req.URL.String(), host) {
			newURL := strings.Replace(req.URL.String(), host, r.fakeURL, 1)
			u, _ := url.Parse(newURL)
			req.URL = u
			req.Host = u.Host
			break
		}
	}
	return r.base.RoundTrip(req)
}

func TestDropboxConnect(t *testing.T) {
	fs := newFakeDropboxServer(t, nil)
	defer fs.Close()
	c := newDropboxTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
}

func TestDropboxConnectMissingToken(t *testing.T) {
	c := NewDropboxConnector(&DropboxConfig{})
	err := c.Connect(context.Background())
	if err == nil || !strings.Contains(err.Error(), "missing access_token") {
		t.Fatalf("expected missing access_token error, got %v", err)
	}
}

func TestDropboxWalkSinglePage(t *testing.T) {
	pages := []dropboxListPage{
		{
			Entries: []dropboxEntry{
				{
					Tag:         "folder",
					Name:        "Reports",
					PathDisplay: "/Reports",
					ID:          "id:fld-1",
				},
				{
					Tag:         "file",
					Name:        "Q1.pdf",
					PathDisplay: "/Reports/Q1.pdf",
					ID:          "id:file-1",
					Size:        1024,
					ContentHash: "abc123",
				},
				{Tag: "deleted", Name: "stale.txt"}, // should be skipped
			},
			HasMore: false,
		},
	}
	fs := newFakeDropboxServer(t, pages)
	defer fs.Close()
	c := newDropboxTestConnector(fs)

	emitted := []*models.EntryRecord{}
	_, err := c.Walk(context.Background(), "/", nil, true, false,
		func(r *models.EntryRecord) error {
			emitted = append(emitted, r)
			return nil
		})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(emitted) != 2 {
		t.Fatalf("want 2 entries (deleted skipped), got %d", len(emitted))
	}
	if emitted[0].Path != "/Reports" || emitted[0].Kind != "directory" {
		t.Errorf("Reports: %+v", emitted[0])
	}
	if emitted[0].NativeID != "id:fld-1" {
		t.Errorf("Reports native_id: %q", emitted[0].NativeID)
	}
	if emitted[1].Path != "/Reports/Q1.pdf" {
		t.Errorf("Q1: %+v", emitted[1])
	}
	if emitted[1].ContentHash != "dropbox:abc123" {
		t.Errorf("Q1 hash: %q (want dropbox: prefix)", emitted[1].ContentHash)
	}
}

func TestDropboxWalkPaginates(t *testing.T) {
	pages := []dropboxListPage{
		{
			Entries: []dropboxEntry{{
				Tag: "file", Name: "a.txt", PathDisplay: "/a.txt", ID: "id:1",
			}},
			HasMore: true,
			Cursor:  "cursor-1",
		},
		{
			Entries: []dropboxEntry{{
				Tag: "file", Name: "b.txt", PathDisplay: "/b.txt", ID: "id:2",
			}},
			HasMore: false,
		},
	}
	fs := newFakeDropboxServer(t, pages)
	defer fs.Close()
	c := newDropboxTestConnector(fs)
	emitted := []*models.EntryRecord{}
	_, err := c.Walk(context.Background(), "/", nil, false, false,
		func(r *models.EntryRecord) error {
			emitted = append(emitted, r)
			return nil
		})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(emitted) != 2 {
		t.Fatalf("want 2 entries from 2 pages, got %d", len(emitted))
	}
	if fs.calls != 2 {
		t.Errorf("expected 2 list_folder calls (one + one continue), got %d", fs.calls)
	}
}

func TestDropboxBuildEntrySkipsDeleted(t *testing.T) {
	rec := buildDropboxEntry(dropboxEntry{Tag: "deleted"}, true)
	if rec != nil {
		t.Errorf("expected nil for deleted, got %+v", rec)
	}
}

func TestDropboxBuildEntryHashOmittedWhenComputeHashFalse(t *testing.T) {
	rec := buildDropboxEntry(dropboxEntry{
		Tag: "file", Name: "x.txt", PathDisplay: "/x.txt",
		ContentHash: "abc",
	}, false)
	if rec == nil || rec.ContentHash != "" {
		t.Errorf("expected empty hash when computeHash=false, got %+v", rec)
	}
}

func TestDropboxRefreshOn401(t *testing.T) {
	// Server returns 401 on first /list_folder call, then 200 with the
	// expected page when called with a "fresh" token.
	calls := 0
	mux := http.NewServeMux()
	mux.HandleFunc("/2/files/list_folder", func(w http.ResponseWriter, r *http.Request) {
		calls++
		auth := r.Header.Get("Authorization")
		if calls == 1 && auth == "Bearer stale" {
			http.Error(w, "expired", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(dropboxListPage{
			Entries: []dropboxEntry{{
				Tag: "file", Name: "ok.txt", PathDisplay: "/ok.txt", ID: "id:1",
			}},
		})
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	c := NewDropboxConnector(&DropboxConfig{AccessToken: "stale"})
	c.httpClient = &http.Client{
		Transport: rewriteDropboxTransport{
			base:    http.DefaultTransport,
			fakeURL: srv.URL,
		},
	}
	c.SetRefreshCallback(func(ctx context.Context) (string, error) {
		return "fresh", nil
	})

	_, err := c.Walk(context.Background(), "/", nil, false, false,
		func(*models.EntryRecord) error { return nil })
	if err != nil {
		t.Fatalf("Walk after refresh: %v", err)
	}
	if calls != 2 {
		t.Errorf("expected 2 calls (401 + retry), got %d", calls)
	}
}

// Smoke-check that ReadFile builds the right header. We don't run a
// real download here; just intercept and assert.
func TestDropboxReadFileSendsAPIArgHeader(t *testing.T) {
	mux := http.NewServeMux()
	var captured string
	mux.HandleFunc("/2/files/download", func(w http.ResponseWriter, r *http.Request) {
		captured = r.Header.Get("Dropbox-API-Arg")
		_, _ = io.WriteString(w, "file-bytes")
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()
	c := NewDropboxConnector(&DropboxConfig{AccessToken: "x"})
	c.httpClient = &http.Client{
		Transport: rewriteDropboxTransport{
			base:    http.DefaultTransport,
			fakeURL: srv.URL,
		},
	}
	body, err := c.ReadFile(context.Background(), "/My Doc.txt")
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	defer body.Close()
	io.Copy(io.Discard, body)
	if !strings.Contains(captured, `"path":"/My Doc.txt"`) {
		t.Errorf("expected Dropbox-API-Arg with path, got %q", captured)
	}
}
