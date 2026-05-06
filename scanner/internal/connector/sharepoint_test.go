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

// fakeSharePointServer covers /sites/{id}, /sites/{id}/drive/items/{id}/...,
// /sites/{id}/drives/{id}/items/{id}/... — the routes SharePoint hits.
type fakeSharePointServer struct {
	*httptest.Server
	siteName    string
	children    map[string][]driveItem
	permissions map[string][]driveItemPermission
}

func newFakeSharePointServer(t *testing.T) *fakeSharePointServer {
	t.Helper()
	fs := &fakeSharePointServer{
		siteName:    "Marketing Team",
		children:    map[string][]driveItem{},
		permissions: map[string][]driveItemPermission{},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/v1.0/sites/", func(w http.ResponseWriter, r *http.Request) {
		// /v1.0/sites/<rest>
		rest := strings.TrimPrefix(r.URL.Path, "/v1.0/sites/")
		switch {
		case strings.Contains(rest, "/drive/items/") || strings.Contains(rest, "/drives/"):
			// items endpoints — same handler logic as OneDrive's
			afterItems := ""
			if i := strings.Index(rest, "/items/"); i >= 0 {
				afterItems = rest[i+len("/items/"):]
			}
			switch {
			case strings.HasSuffix(afterItems, "/children"):
				id := strings.TrimSuffix(afterItems, "/children")
				id, _ = url.PathUnescape(id)
				_ = json.NewEncoder(w).Encode(driveItemListPage{Value: fs.children[id]})
			case strings.HasSuffix(afterItems, "/permissions"):
				id := strings.TrimSuffix(afterItems, "/permissions")
				id, _ = url.PathUnescape(id)
				_ = json.NewEncoder(w).Encode(driveItemPermissionPage{Value: fs.permissions[id]})
			default:
				id, _ := url.PathUnescape(afterItems)
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
		default:
			// /v1.0/sites/{id} — return site details.
			_ = json.NewEncoder(w).Encode(map[string]string{
				"id":          "site-id-stub",
				"displayName": fs.siteName,
				"name":        "marketing",
			})
		}
	})
	fs.Server = httptest.NewServer(mux)
	return fs
}

func newSharePointTestConnector(srv *fakeSharePointServer) *SharePointConnector {
	c := NewSharePointConnector(&SharePointConfig{
		AccessToken: "test-token",
		SiteID:      "site-id-stub",
	})
	transport := http.DefaultTransport.(*http.Transport).Clone()
	c.httpClient = &http.Client{
		Transport: rewriteOneDriveTransport{base: transport, fakeURL: srv.URL},
	}
	return c
}

func TestSharePointConnect(t *testing.T) {
	fs := newFakeSharePointServer(t)
	defer fs.Close()
	c := newSharePointTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
	if c.rootDisplayName != "Marketing Team" {
		t.Errorf("rootDisplayName: %q", c.rootDisplayName)
	}
}

func TestSharePointConnectMissingSiteID(t *testing.T) {
	c := NewSharePointConnector(&SharePointConfig{AccessToken: "x"})
	err := c.Connect(context.Background())
	if err == nil || !strings.Contains(err.Error(), "missing site_id") {
		t.Fatalf("expected missing site_id error, got %v", err)
	}
}

func TestSharePointConnectMissingToken(t *testing.T) {
	c := NewSharePointConnector(&SharePointConfig{SiteID: "x"})
	err := c.Connect(context.Background())
	if err == nil || !strings.Contains(err.Error(), "missing access_token") {
		t.Fatalf("expected missing access_token error, got %v", err)
	}
}

func TestSharePointWalkBuildsPaths(t *testing.T) {
	fs := newFakeSharePointServer(t)
	defer fs.Close()
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
	}{MimeType: "application/pdf"}
	file.Hashes.SHA1Hash = "DEF456"
	fs.children["root"] = []driveItem{
		{ID: "fld-1", Name: "Reports", Folder: &folder},
	}
	fs.children["fld-1"] = []driveItem{
		{ID: "file-1", Name: "Q1.pdf", Size: 2048, File: &file},
	}
	c := newSharePointTestConnector(fs)
	if err := c.Connect(context.Background()); err != nil {
		t.Fatalf("Connect: %v", err)
	}
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
	if len(emitted) != 3 {
		t.Fatalf("want 3 entries, got %d", len(emitted))
	}
	if emitted[0].Path != "/Marketing Team" {
		t.Errorf("root: %+v", emitted[0])
	}
	if emitted[1].Path != "/Marketing Team/Reports" {
		t.Errorf("Reports: %+v", emitted[1])
	}
	if emitted[2].Path != "/Marketing Team/Reports/Q1.pdf" {
		t.Errorf("Q1: %+v", emitted[2])
	}
	if emitted[2].ContentHash != "sha1:def456" {
		t.Errorf("Q1 hash: %q", emitted[2].ContentHash)
	}
}

func TestSharePointDriveBaseToggles(t *testing.T) {
	c1 := NewSharePointConnector(&SharePointConfig{
		AccessToken: "x", SiteID: "site-1",
	})
	if got := c1.driveBase(); got != "/sites/site-1/drive" {
		t.Errorf("default drive: %q", got)
	}
	c2 := NewSharePointConnector(&SharePointConfig{
		AccessToken: "x", SiteID: "site-1", DriveID: "drv-7",
	})
	if got := c2.driveBase(); got != "/sites/site-1/drives/drv-7" {
		t.Errorf("explicit drive: %q", got)
	}
}
