package connector

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fakeOneDriveServer minimally implements the Graph endpoints we use:
// /me, /me/drive/items/{id}, /me/drive/items/{id}/children,
// /me/drive/items/{id}/permissions.
type fakeOneDriveServer struct {
	*httptest.Server
	children    map[string][]driveItem            // itemID -> children list
	permissions map[string][]driveItemPermission // itemID -> permissions list
}

func newFakeOneDriveServer(t *testing.T) *fakeOneDriveServer {
	t.Helper()
	fs := &fakeOneDriveServer{
		children:    map[string][]driveItem{},
		permissions: map[string][]driveItemPermission{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1.0/me", func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
			http.Error(w, "auth", http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"mail": "alice@contoso.com", "userPrincipalName": "alice@contoso.com",
		})
	})
	mux.HandleFunc("/v1.0/me/drive/items/", func(w http.ResponseWriter, r *http.Request) {
		// Endpoints: /v1.0/me/drive/items/{id}/children
		//            /v1.0/me/drive/items/{id}/permissions
		//            /v1.0/me/drive/items/{id}  (single-item GET)
		path := strings.TrimPrefix(r.URL.Path, "/v1.0/me/drive/items/")
		switch {
		case strings.HasSuffix(path, "/children"):
			id := strings.TrimSuffix(path, "/children")
			id, _ = url.PathUnescape(id)
			page := driveItemListPage{Value: fs.children[id]}
			_ = json.NewEncoder(w).Encode(page)
		case strings.HasSuffix(path, "/permissions"):
			id := strings.TrimSuffix(path, "/permissions")
			id, _ = url.PathUnescape(id)
			page := driveItemPermissionPage{Value: fs.permissions[id]}
			_ = json.NewEncoder(w).Encode(page)
		default:
			id, _ := url.PathUnescape(path)
			// Walk the children index for any folder that might
			// expose this id directly. Test setup is responsible for
			// registering names where needed.
			for _, kids := range fs.children {
				for _, c := range kids {
					if c.ID == id {
						_ = json.NewEncoder(w).Encode(c)
						return
					}
				}
			}
			http.Error(w, "not found", 404)
		}
	})
	fs.Server = httptest.NewServer(mux)
	return fs
}

func newOneDriveTestConnector(srv *fakeOneDriveServer) *OneDriveConnector {
	c := NewOneDriveConnector(&OneDriveConfig{AccessToken: "test-token"})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteOneDriveTransport{
			base:    transport,
			fakeURL: srv.URL,
		},
	}
	return c
}

type rewriteOneDriveTransport struct {
	base    http.RoundTripper
	fakeURL string
}

func (r rewriteOneDriveTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if strings.HasPrefix(req.URL.String(), "https://graph.microsoft.com") {
		newURL := strings.Replace(req.URL.String(), "https://graph.microsoft.com", r.fakeURL, 1)
		u, _ := url.Parse(newURL)
		req.URL = u
		req.Host = u.Host
	}
	return r.base.RoundTrip(req)
}

func TestOneDriveConnect(t *testing.T) {
	fs := newFakeOneDriveServer(t)
	defer fs.Close()
	c := newOneDriveTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
}

func TestOneDriveConnectMissingToken(t *testing.T) {
	c := NewOneDriveConnector(&OneDriveConfig{})
	if err := c.Connect(context.Background()); err == nil ||
		!strings.Contains(err.Error(), "missing access_token") {
		t.Fatalf("expected missing token error, got %v", err)
	}
}

func TestOneDriveWalkBuildsPaths(t *testing.T) {
	fs := newFakeOneDriveServer(t)
	defer fs.Close()
	// Tree:
	//   root/
	//     Reports/
	//       Q1.docx (sha1=abc)
	int64Ptr := func(n int64) *int64 { v := n; _ = v; return &n }
	_ = int64Ptr
	folder := struct {
		ChildCount int `json:"childCount"`
	}{ChildCount: 1}
	file := struct {
		MimeType string `json:"mimeType"`
		Hashes   struct {
			SHA1Hash     string `json:"sha1Hash"`
			SHA256Hash   string `json:"sha256Hash"`
			QuickXorHash string `json:"quickXorHash"`
			Crc32Hash    string `json:"crc32Hash"`
		} `json:"hashes"`
	}{
		MimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	}
	file.Hashes.SHA1Hash = "ABC123"
	fs.children["root"] = []driveItem{
		{ID: "fld-1", Name: "Reports", Folder: &folder},
	}
	fs.children["fld-1"] = []driveItem{
		{ID: "file-1", Name: "Q1.docx", Size: 1024, File: &file},
	}
	c := newOneDriveTestConnector(fs)
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
	// Root + Reports + Q1.
	if len(emitted) != 3 {
		t.Fatalf("want 3 entries, got %d", len(emitted))
	}
	if emitted[0].Path != "/OneDrive" || emitted[0].Kind != "directory" {
		t.Errorf("root: %+v", emitted[0])
	}
	if emitted[1].Path != "/OneDrive/Reports" || emitted[1].NativeID != "fld-1" {
		t.Errorf("Reports: %+v", emitted[1])
	}
	if emitted[2].Path != "/OneDrive/Reports/Q1.docx" {
		t.Errorf("Q1: %+v", emitted[2])
	}
	// SHA-1 lowercased + sha1: prefix.
	if emitted[2].ContentHash != "sha1:abc123" {
		t.Errorf("Q1 hash: %q", emitted[2].ContentHash)
	}
}

func TestOneDriveBuildACLUserGrant(t *testing.T) {
	user := struct {
		ID          string `json:"id"`
		Email       string `json:"email"`
		DisplayName string `json:"displayName"`
	}{ID: "u-1", Email: "alice@contoso.com", DisplayName: "Alice"}
	perms := []driveItemPermission{
		{
			ID:    "perm-1",
			Roles: []string{"write"},
			GrantedToV2: &driveItemGrantedToIdentitySet{
				User: &user,
			},
		},
	}
	acl := buildOneDriveACL(perms)
	if acl == nil || acl.Type != "cloud_drive" {
		t.Fatalf("acl: %+v", acl)
	}
	if len(acl.CloudDriveGrants) != 1 {
		t.Fatalf("want 1 grant, got %d", len(acl.CloudDriveGrants))
	}
	g := acl.CloudDriveGrants[0]
	if g.Role != "writer" {
		t.Errorf("role: %q", g.Role)
	}
	if g.Principal.Email != "alice@contoso.com" {
		t.Errorf("principal email lost: %+v", g.Principal)
	}
}

func TestOneDriveBuildACLAnonymousLink(t *testing.T) {
	perms := []driveItemPermission{
		{
			ID:    "perm-link",
			Roles: []string{"read"},
			Link:  &driveItemSharingLink{Type: "view", Scope: "anonymous"},
		},
	}
	acl := buildOneDriveACL(perms)
	if acl == nil || len(acl.CloudDriveGrants) != 1 {
		t.Fatalf("acl: %+v", acl)
	}
	g := acl.CloudDriveGrants[0]
	if g.Principal.Type != "anyone" {
		t.Errorf("anon link should map to anyone principal, got %q", g.Principal.Type)
	}
	if g.Link == nil || g.Link.Scope != "anyone" {
		t.Errorf("link scope: %+v", g.Link)
	}
}

func TestOneDriveBuildACLOrganizationLink(t *testing.T) {
	perms := []driveItemPermission{
		{
			ID:    "perm-org",
			Roles: []string{"read"},
			Link:  &driveItemSharingLink{Type: "view", Scope: "organization"},
		},
	}
	acl := buildOneDriveACL(perms)
	if acl == nil || len(acl.CloudDriveGrants) != 1 {
		t.Fatalf("acl: %+v", acl)
	}
	g := acl.CloudDriveGrants[0]
	if g.Principal.Type != "domain" {
		t.Errorf("organization link should map to domain principal, got %q", g.Principal.Type)
	}
}

// v0.22.0 — parallel-fan-out validation. The walker fans out the
// per-item /permissions calls; the test injects an artificial delay
// per /permissions request and asserts wall-clock < serial baseline.
// 200 items × 50ms serial = 10s; with 8 workers it should be ~1.3s,
// so 3s leaves plenty of CI headroom.
func TestOneDriveWalkParallelizesPermissionFetches(t *testing.T) {
	if testing.Short() {
		t.Skip("perf-shape test")
	}
	fs := newFakeOneDriveServer(t)
	defer fs.Close()

	// Wrap the existing handler with a per-/permissions latency
	// injector. Doing it here rather than mutating the constructor
	// keeps the rest of the test fixtures untouched.
	const perItemDelay = 50 * time.Millisecond
	const itemCount = 200
	delayMux := http.NewServeMux()
	delayMux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/permissions") {
			time.Sleep(perItemDelay)
		}
		fs.Server.Config.Handler.ServeHTTP(w, r)
	})
	delaySrv := httptest.NewServer(delayMux)
	defer delaySrv.Close()

	// Register 200 file children + empty permissions for each.
	kids := make([]driveItem, 0, itemCount)
	for i := 0; i < itemCount; i++ {
		id := fmt.Sprintf("file-%d", i)
		kids = append(kids, driveItem{
			ID:   id,
			Name: fmt.Sprintf("file-%d.txt", i),
			Size: 100,
			File: &struct {
				MimeType string `json:"mimeType"`
				Hashes   struct {
					SHA1Hash     string `json:"sha1Hash"`
					SHA256Hash   string `json:"sha256Hash"`
					QuickXorHash string `json:"quickXorHash"`
					Crc32Hash    string `json:"crc32Hash"`
				} `json:"hashes"`
			}{},
		})
		fs.permissions[id] = nil
	}
	fs.children["root"] = kids

	c := NewOneDriveConnector(&OneDriveConfig{AccessToken: "test-token"})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteOneDriveTransport{base: transport, fakeURL: delaySrv.URL},
	}

	start := time.Now()
	emitted := 0
	_, err := c.Walk(
		context.Background(), "/", nil, false, false,
		func(r *models.EntryRecord) error { emitted++; return nil },
	)
	elapsed := time.Since(start)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	if emitted != itemCount+1 { // +1 for the root entry
		t.Fatalf("want %d emitted, got %d", itemCount+1, emitted)
	}
	// Generous bound: with 8 workers and 50ms/perm, expected ~1.3s.
	// Serial baseline is 10s+. 3s catches the regression.
	if elapsed > 3*time.Second {
		t.Errorf("parallel walk took %v, want < 3s — fan-out may have regressed", elapsed)
	}
}

// v0.22.0 — sanity check that the post-fan-out emit order still
// matches the API response order. Caller-visible callback ordering is
// load-bearing for tag-inheritance and ACL-denorm assumptions.
func TestOneDriveWalkPreservesChildOrder(t *testing.T) {
	fs := newFakeOneDriveServer(t)
	defer fs.Close()
	names := []string{"alpha.txt", "beta.txt", "gamma.txt", "delta.txt", "epsilon.txt"}
	kids := []driveItem{}
	for i, name := range names {
		id := fmt.Sprintf("file-%d", i)
		kids = append(kids, driveItem{
			ID:   id,
			Name: name,
			Size: 1,
			File: &struct {
				MimeType string `json:"mimeType"`
				Hashes   struct {
					SHA1Hash     string `json:"sha1Hash"`
					SHA256Hash   string `json:"sha256Hash"`
					QuickXorHash string `json:"quickXorHash"`
					Crc32Hash    string `json:"crc32Hash"`
				} `json:"hashes"`
			}{},
		})
		fs.permissions[id] = nil
	}
	fs.children["root"] = kids

	c := newOneDriveTestConnector(fs)
	got := []string{}
	_, err := c.Walk(
		context.Background(), "/", nil, false, false,
		func(r *models.EntryRecord) error {
			if r.Kind == "file" {
				got = append(got, r.Name)
			}
			return nil
		},
	)
	if err != nil {
		t.Fatalf("Walk: %v", err)
	}
	for i, want := range names {
		if i >= len(got) || got[i] != want {
			t.Errorf("at index %d: got %q, want %q (full=%v)", i, got[i], want, got)
		}
	}
}

func TestMapOneDriveRolePicksStrongest(t *testing.T) {
	cases := []struct {
		in   []string
		want string
	}{
		{[]string{"read"}, "reader"},
		{[]string{"write"}, "writer"},
		{[]string{"owner"}, "owner"},
		{[]string{"read", "write"}, "writer"},
		{[]string{"read", "owner"}, "owner"},
		{[]string{}, ""},
		{[]string{"unknown-role"}, ""},
	}
	for _, tc := range cases {
		got := mapOneDriveRole(tc.in)
		if got != tc.want {
			t.Errorf("mapOneDriveRole(%v) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
