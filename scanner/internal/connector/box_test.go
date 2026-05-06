package connector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fakeBoxServer mocks /2.0/users/me, /2.0/folders/{id}/items, and
// /2.0/{files,folders}/{id}/collaborations.
type fakeBoxServer struct {
	*httptest.Server
	children     map[string][]boxItem            // folder_id -> children
	folderNames  map[string]string               // folder_id -> name
	collaborats  map[string][]boxCollaboration   // "{type}:{id}" -> collabs
}

func newFakeBoxServer(t *testing.T) *fakeBoxServer {
	t.Helper()
	fs := &fakeBoxServer{
		children:    map[string][]boxItem{},
		folderNames: map[string]string{},
		collaborats: map[string][]boxCollaboration{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/2.0/users/me", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			http.Error(w, "auth", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"login": "alice@example.com", "name": "Alice",
		})
	})
	mux.HandleFunc("/2.0/folders/", func(w http.ResponseWriter, r *http.Request) {
		// /2.0/folders/{id}/items, /2.0/folders/{id}/collaborations,
		// or single-folder /2.0/folders/{id}.
		rest := strings.TrimPrefix(r.URL.Path, "/2.0/folders/")
		switch {
		case strings.HasSuffix(rest, "/items"):
			id := strings.TrimSuffix(rest, "/items")
			id, _ = url.PathUnescape(id)
			children := fs.children[id]
			off, _ := strconv.Atoi(r.URL.Query().Get("offset"))
			lim, _ := strconv.Atoi(r.URL.Query().Get("limit"))
			if lim == 0 {
				lim = 100
			}
			end := off + lim
			if end > len(children) {
				end = len(children)
			}
			page := boxItemListPage{
				Entries:    children[off:end],
				Offset:     off,
				Limit:      lim,
				TotalCount: len(children),
			}
			_ = json.NewEncoder(w).Encode(page)
		case strings.HasSuffix(rest, "/collaborations"):
			id := strings.TrimSuffix(rest, "/collaborations")
			id, _ = url.PathUnescape(id)
			page := boxCollaborationPage{Entries: fs.collaborats["folder:"+id]}
			_ = json.NewEncoder(w).Encode(page)
		default:
			id, _ := url.PathUnescape(rest)
			name := fs.folderNames[id]
			if name == "" {
				name = "All Files"
			}
			_ = json.NewEncoder(w).Encode(boxItem{
				Type: "folder", ID: id, Name: name,
			})
		}
	})
	mux.HandleFunc("/2.0/files/", func(w http.ResponseWriter, r *http.Request) {
		rest := strings.TrimPrefix(r.URL.Path, "/2.0/files/")
		if strings.HasSuffix(rest, "/collaborations") {
			id := strings.TrimSuffix(rest, "/collaborations")
			id, _ = url.PathUnescape(id)
			page := boxCollaborationPage{Entries: fs.collaborats["file:"+id]}
			_ = json.NewEncoder(w).Encode(page)
			return
		}
		http.Error(w, "not found", 404)
	})
	fs.Server = httptest.NewServer(mux)
	return fs
}

func newBoxTestConnector(srv *fakeBoxServer) *BoxConnector {
	c := NewBoxConnector(&BoxConfig{AccessToken: "test-token"})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteBoxTransport{base: transport, fakeURL: srv.URL},
	}
	return c
}

type rewriteBoxTransport struct {
	base    http.RoundTripper
	fakeURL string
}

func (r rewriteBoxTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if strings.HasPrefix(req.URL.String(), "https://api.box.com") {
		newURL := strings.Replace(req.URL.String(), "https://api.box.com", r.fakeURL, 1)
		u, _ := url.Parse(newURL)
		req.URL = u
		req.Host = u.Host
	}
	return r.base.RoundTrip(req)
}

func TestBoxConnect(t *testing.T) {
	fs := newFakeBoxServer(t)
	defer fs.Close()
	c := newBoxTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
}

func TestBoxConnectMissingToken(t *testing.T) {
	c := NewBoxConnector(&BoxConfig{})
	err := c.Connect(context.Background())
	if err == nil || !strings.Contains(err.Error(), "missing access_token") {
		t.Fatalf("expected missing access_token error, got %v", err)
	}
}

func TestBoxWalkBuildsPaths(t *testing.T) {
	fs := newFakeBoxServer(t)
	defer fs.Close()
	fs.children["0"] = []boxItem{
		{Type: "folder", ID: "fld-1", Name: "Reports"},
	}
	fs.children["fld-1"] = []boxItem{
		{
			Type: "file", ID: "file-1", Name: "Q1.pdf",
			Size: 1024, Sha1: "ABCDEF",
		},
	}
	c := newBoxTestConnector(fs)
	emitted := []*models.EntryRecord{}
	_, err := c.Walk(context.Background(), "/", nil, true, false,
		func(r *models.EntryRecord) error {
			emitted = append(emitted, r)
			return nil
		})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if len(emitted) != 3 {
		t.Fatalf("want 3 entries, got %d", len(emitted))
	}
	if emitted[0].Path != "/All Files" {
		t.Errorf("root: %+v", emitted[0])
	}
	if emitted[1].Path != "/All Files/Reports" || emitted[1].NativeID != "fld-1" {
		t.Errorf("Reports: %+v", emitted[1])
	}
	if emitted[2].Path != "/All Files/Reports/Q1.pdf" {
		t.Errorf("Q1: %+v", emitted[2])
	}
	if emitted[2].ContentHash != "sha1:abcdef" {
		t.Errorf("Q1 hash: %q", emitted[2].ContentHash)
	}
}

func TestBoxWalkPaginates(t *testing.T) {
	fs := newFakeBoxServer(t)
	defer fs.Close()
	// 250 children — at limit=200, that's two pages.
	for i := 0; i < 250; i++ {
		fs.children["0"] = append(fs.children["0"], boxItem{
			Type: "file", ID: "f" + strconv.Itoa(i),
			Name: "file" + strconv.Itoa(i) + ".txt",
		})
	}
	c := newBoxTestConnector(fs)
	count := 0
	_, err := c.Walk(context.Background(), "/", nil, false, false,
		func(r *models.EntryRecord) error {
			if r.Kind == "file" {
				count++
			}
			return nil
		})
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if count != 250 {
		t.Errorf("want 250 files via pagination, got %d", count)
	}
}

func TestBoxBuildACLMapsRoles(t *testing.T) {
	collabs := []boxCollaboration{
		{Role: "owner", Status: "accepted", AccessibleBy: &struct {
			Type  string `json:"type"`
			ID    string `json:"id"`
			Name  string `json:"name"`
			Login string `json:"login"`
		}{Type: "user", ID: "u-1", Name: "Alice", Login: "alice@example.com"}},
		{Role: "editor", Status: "accepted", AccessibleBy: &struct {
			Type  string `json:"type"`
			ID    string `json:"id"`
			Name  string `json:"name"`
			Login string `json:"login"`
		}{Type: "user", ID: "u-2", Login: "bob@example.com"}},
		{Role: "viewer", Status: "accepted", AccessibleBy: &struct {
			Type  string `json:"type"`
			ID    string `json:"id"`
			Name  string `json:"name"`
			Login string `json:"login"`
		}{Type: "group", ID: "g-1", Name: "Marketing"}},
		{Role: "viewer", Status: "pending", AccessibleBy: &struct {
			Type  string `json:"type"`
			ID    string `json:"id"`
			Name  string `json:"name"`
			Login string `json:"login"`
		}{Type: "user", ID: "u-3"}}, // pending — should be skipped
	}
	acl := buildBoxACL(collabs)
	if acl == nil || acl.Type != "cloud_drive" {
		t.Fatalf("acl: %+v", acl)
	}
	if len(acl.CloudDriveGrants) != 3 {
		t.Fatalf("want 3 grants (pending skipped), got %d", len(acl.CloudDriveGrants))
	}
	if acl.CloudDriveGrants[0].Role != "owner" {
		t.Errorf("first role: %q", acl.CloudDriveGrants[0].Role)
	}
	if acl.CloudDriveGrants[1].Role != "writer" {
		t.Errorf("editor should map to writer, got %q", acl.CloudDriveGrants[1].Role)
	}
	if acl.CloudDriveGrants[2].Role != "reader" {
		t.Errorf("viewer should map to reader, got %q", acl.CloudDriveGrants[2].Role)
	}
	if acl.CloudDriveGrants[2].Principal.Type != "group" {
		t.Errorf("group principal lost: %+v", acl.CloudDriveGrants[2].Principal)
	}
}

func TestMapBoxRole(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"owner", "owner"},
		{"co-owner", "owner"},
		{"editor", "writer"},
		{"viewer_uploader", "writer"},
		{"previewer_uploader", "writer"},
		{"uploader", "writer"},
		{"viewer", "reader"},
		{"previewer", "reader"},
		{"", ""},
		{"future-role", "reader"}, // unknown floors to reader
	}
	for _, tc := range cases {
		got := mapBoxRole(tc.in)
		if got != tc.want {
			t.Errorf("mapBoxRole(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
