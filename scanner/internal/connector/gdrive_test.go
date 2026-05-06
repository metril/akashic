package connector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fakeDriveServer minimally implements the Drive v3 endpoints we use:
// about.get, files.list (paged 'parent in parents' filter), and
// files.get (?fields=name).
type fakeDriveServer struct {
	*httptest.Server
	files map[string][]driveFile // parentID -> children
	names map[string]string      // id -> name (folder name resolution)
	hits  atomic.Int64
}

func newFakeDriveServer(t *testing.T) *fakeDriveServer {
	t.Helper()
	fs := &fakeDriveServer{
		files: map[string][]driveFile{},
		names: map[string]string{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/drive/v3/about", func(w http.ResponseWriter, r *http.Request) {
		fs.hits.Add(1)
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			http.Error(w, "auth", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"user": map[string]string{"emailAddress": "alice@example.com"},
		})
	})
	mux.HandleFunc("/drive/v3/files", func(w http.ResponseWriter, r *http.Request) {
		fs.hits.Add(1)
		q := r.URL.Query().Get("q")
		// Parse parent id out of "'<id>' in parents and trashed = false".
		i, j := strings.Index(q, "'"), strings.Index(q, "' in parents")
		if i < 0 || j < 0 || i >= j {
			http.Error(w, "bad q", 400)
			return
		}
		parent := q[i+1 : j]
		page := driveListPage{Files: fs.files[parent]}
		_ = json.NewEncoder(w).Encode(page)
	})
	mux.HandleFunc("/drive/v3/files/", func(w http.ResponseWriter, r *http.Request) {
		fs.hits.Add(1)
		// Strip "/drive/v3/files/" prefix to get the id.
		id := strings.TrimPrefix(r.URL.Path, "/drive/v3/files/")
		// Decode percent-escaped IDs (test uses simple IDs; this keeps
		// the helper robust if a test uses one with slashes).
		if d, err := url.PathUnescape(id); err == nil {
			id = d
		}
		name, ok := fs.names[id]
		if !ok {
			http.Error(w, "not found", 404)
			return
		}
		_ = json.NewEncoder(w).Encode(driveFile{ID: id, Name: name})
	})
	fs.Server = httptest.NewServer(mux)
	return fs
}

// withRedirectedDrive overrides the connector's baseURL by post-
// processing every URL through a string replacement. The connector
// hardcodes googleapis.com URLs; for tests we need to point at the
// httptest server. We accomplish that via a custom RoundTripper.
func newGDriveTestConnector(srv *fakeDriveServer) *GDriveConnector {
	c := NewGDriveConnector(&GDriveConfig{AccessToken: "test-token"})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteTransport{
			base:    transport,
			fakeURL: srv.URL,
		},
	}
	return c
}

type rewriteTransport struct {
	base    http.RoundTripper
	fakeURL string
}

func (r rewriteTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if strings.HasPrefix(req.URL.String(), "https://www.googleapis.com") {
		newURL := strings.Replace(req.URL.String(), "https://www.googleapis.com", r.fakeURL, 1)
		u, _ := url.Parse(newURL)
		req.URL = u
		req.Host = u.Host
	}
	return r.base.RoundTrip(req)
}

func TestGDriveConnect(t *testing.T) {
	fs := newFakeDriveServer(t)
	defer fs.Close()
	c := newGDriveTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
}

func TestGDriveConnectMissingToken(t *testing.T) {
	c := NewGDriveConnector(&GDriveConfig{})
	err := c.Connect(context.Background())
	if err == nil || !strings.Contains(err.Error(), "missing access_token") {
		t.Fatalf("expected missing token error, got %v", err)
	}
}

func TestGDriveWalkBuildsSyntheticPaths(t *testing.T) {
	fs := newFakeDriveServer(t)
	defer fs.Close()

	// Tree:
	//   root/
	//     Reports/  (folder, id=fld-1)
	//       Q1.pdf  (id=file-1, md5=abc)
	//       Q2.pdf  (id=file-2)
	fs.files["root"] = []driveFile{
		{ID: "fld-1", Name: "Reports", MimeType: driveFolderMime},
	}
	fs.files["fld-1"] = []driveFile{
		{ID: "file-1", Name: "Q1.pdf", MimeType: "application/pdf",
			Size: "1024", Md5Checksum: "abc"},
		{ID: "file-2", Name: "Q2.pdf", MimeType: "application/pdf",
			Size: "2048"},
	}
	c := newGDriveTestConnector(fs)
	emitted := []*models.EntryRecord{}
	_, err := c.Walk(
		context.Background(), "/", nil, true, false,
		func(r *models.EntryRecord) error {
			emitted = append(emitted, r)
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	// Root + Reports + Q1 + Q2.
	if len(emitted) != 4 {
		t.Fatalf("want 4 entries, got %d: %+v", len(emitted), emitted)
	}
	if emitted[0].Path != "/My Drive" || emitted[0].Kind != "directory" {
		t.Errorf("root: %+v", emitted[0])
	}
	if emitted[1].Path != "/My Drive/Reports" || emitted[1].NativeID != "fld-1" {
		t.Errorf("Reports: %+v", emitted[1])
	}
	if emitted[2].Path != "/My Drive/Reports/Q1.pdf" {
		t.Errorf("Q1: %+v", emitted[2])
	}
	if emitted[2].ContentHash != "md5:abc" {
		t.Errorf("Q1 hash: %q", emitted[2].ContentHash)
	}
	if emitted[2].NativeID != "file-1" {
		t.Errorf("Q1 native_id: %q", emitted[2].NativeID)
	}
}

func TestGDriveWalkNameCollisionAppendsID(t *testing.T) {
	fs := newFakeDriveServer(t)
	defer fs.Close()

	// Two siblings share the name "Notes.txt"; first keeps bare name,
	// second gets " (id)" suffix.
	fs.files["root"] = []driveFile{
		{ID: "f1", Name: "Notes.txt", MimeType: "text/plain"},
		{ID: "f2", Name: "Notes.txt", MimeType: "text/plain"},
	}
	c := newGDriveTestConnector(fs)
	paths := []string{}
	_, err := c.Walk(
		context.Background(), "/", nil, false, false,
		func(r *models.EntryRecord) error {
			paths = append(paths, r.Path)
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	// Order is root, Notes.txt, Notes.txt (f2)
	if len(paths) != 3 {
		t.Fatalf("want 3 paths, got %v", paths)
	}
	if paths[1] != "/My Drive/Notes.txt" {
		t.Errorf("first sibling path: %q", paths[1])
	}
	if paths[2] != "/My Drive/Notes.txt (f2)" {
		t.Errorf("second sibling path: %q", paths[2])
	}
}

func TestGDriveBuildACL(t *testing.T) {
	f := driveFile{
		Permissions: []drivePermission{
			{ID: "p1", Type: "user", Role: "writer",
				EmailAddress: "alice@example.com", DisplayName: "Alice"},
			{ID: "p2", Type: "anyone", Role: "reader"},
			{ID: "p3", Type: "domain", Role: "reader", Domain: "example.com"},
			{ID: "p4", Type: "user", Role: "owner",
				EmailAddress: "owner@example.com",
				InheritedPermission: true,
				InheritedFromID:     "fld-1"},
		},
	}
	acl := buildGDriveACL(f)
	if acl == nil || acl.Type != "cloud_drive" {
		t.Fatalf("acl: %+v", acl)
	}
	if len(acl.CloudDriveGrants) != 4 {
		t.Fatalf("want 4 grants, got %d", len(acl.CloudDriveGrants))
	}
	if acl.CloudDriveGrants[0].Role != "writer" {
		t.Errorf("first role: %q", acl.CloudDriveGrants[0].Role)
	}
	if acl.CloudDriveGrants[3].Inherited != true {
		t.Errorf("inherited flag lost: %+v", acl.CloudDriveGrants[3])
	}
	if acl.CloudDriveGrants[3].InheritedFromID != "fld-1" {
		t.Errorf("inherited_from_id lost")
	}
	// MarshalJSON should round-trip — discriminator + grants.
	b, err := acl.MarshalJSON()
	if err != nil {
		t.Fatalf("MarshalJSON: %v", err)
	}
	if !strings.Contains(string(b), `"type":"cloud_drive"`) {
		t.Errorf("missing discriminator in JSON: %s", b)
	}
}

func TestStripCollisionHint(t *testing.T) {
	cases := []struct {
		in       string
		wantName string
		wantID   string
	}{
		{"Foo (abc-123)", "Foo", "abc-123"},
		{"Foo (1aBcD-XyZ_99)", "Foo", "1aBcD-XyZ_99"},
		{"Foo", "Foo", ""},
		{"Foo (with spaces)", "Foo (with spaces)", ""}, // not an id-shaped suffix
		{"Foo ()", "Foo ()", ""},                        // empty hint, ignored
	}
	for _, tc := range cases {
		gotName, gotID := stripCollisionHint(tc.in)
		if gotName != tc.wantName || gotID != tc.wantID {
			t.Errorf("stripCollisionHint(%q) = (%q,%q), want (%q,%q)",
				tc.in, gotName, gotID, tc.wantName, tc.wantID)
		}
	}
}
