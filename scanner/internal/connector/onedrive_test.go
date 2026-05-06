package connector

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

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
