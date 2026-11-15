package client

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/cli/internal/apiclient"
)

const (
	idSrc  = "11111111-1111-4111-8111-111111111111"
	idScan = "22222222-2222-4222-8222-222222222222"
)

var scanID = uuid.MustParse(idScan)

// writeJSON answers like the real API: a JSON content type, which the
// generated client needs to decode the body.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func newClient(t *testing.T, url string) *Client {
	t.Helper()
	c, err := New(url, "test-key")
	if err != nil {
		t.Fatal(err)
	}
	return c
}

func TestClient_Search(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/search" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth header: %q", got)
		}
		if q := r.URL.Query().Get("q"); q != "report" {
			t.Errorf("expected query 'report', got '%s'", q)
		}
		if ext := r.URL.Query().Get("extension"); ext != "pdf" {
			t.Errorf("expected extension 'pdf', got '%s'", ext)
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{
			"items": []map[string]interface{}{
				{"id": idSrc, "filename": "report.pdf", "path": "/data/report.pdf", "dup_count": 0, "tags": []string{}},
			},
			"total": 1,
			"limit": 20,
			"query": "report",
		})
	}))
	defer server.Close()

	c := newClient(t, server.URL)
	ext := "pdf"
	results, err := c.Search(context.Background(), "report", apiclient.SearchParams{Extension: &ext})
	if err != nil {
		t.Fatal(err)
	}
	if len(results.Items) != 1 || results.Items[0].Path != "/data/report.pdf" {
		t.Errorf("unexpected results: %+v", results)
	}
}

func TestClient_ListSources(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, []map[string]interface{}{
			{"id": idSrc, "name": "nas1", "type": "smb", "status": "online"},
		})
	}))
	defer server.Close()

	sources, err := newClient(t, server.URL).ListSources(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(sources) != 1 || sources[0].Name != "nas1" {
		t.Errorf("unexpected sources: %+v", sources)
	}
}

// GetScan + CancelScan are the endpoints backing `scan wait` and
// `scan cancel`. Smoke-test happy path + 404 → APIError surface so the
// CLI exit-code mapping in commands/scans.go has something to react to.
func TestClient_GetScan(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/scans/"+idScan {
			writeJSON(w, http.StatusOK, map[string]interface{}{
				"id": idScan, "source_id": idSrc, "status": "completed",
				"files_found": 12, "scan_type": "full",
			})
			return
		}
		writeJSON(w, http.StatusNotFound, map[string]interface{}{
			"code": "not_found", "message": "scan not found", "request_id": "r",
		})
	}))
	defer server.Close()

	c := newClient(t, server.URL)
	s, err := c.GetScan(context.Background(), scanID)
	if err != nil {
		t.Fatalf("happy path: %v", err)
	}
	if s.Status != "completed" {
		t.Errorf("status: got %q, want completed", s.Status)
	}

	_, err = c.GetScan(context.Background(), uuid.New())
	if err == nil {
		t.Fatal("expected error on 404")
	}
	var apiErr *APIError
	if !errors.As(err, &apiErr) || apiErr.Status != 404 {
		t.Fatalf("expected APIError status=404, got %T %v", err, err)
	}
	if got := err.Error(); got != "API error 404: scan not found" {
		t.Errorf("envelope message not surfaced: %q", got)
	}
}

func TestClient_CancelScan(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		writeJSON(w, http.StatusOK, map[string]string{"scan_id": idScan, "status": "cancelled"})
	}))
	defer server.Close()
	if err := newClient(t, server.URL).CancelScan(context.Background(), scanID); err != nil {
		t.Fatal(err)
	}
	if gotPath != "/api/v1/scans/"+idScan+"/cancel" {
		t.Errorf("expected /api/v1/scans/%s/cancel, got %s", idScan, gotPath)
	}
}

func TestAPIMessage(t *testing.T) {
	if got := apiMessage(`{"code":"not_found","message":"scan not found","detail":null,"request_id":"r1"}`); got != "scan not found" {
		t.Errorf("envelope: got %q", got)
	}
	if got := apiMessage("upstream timeout"); got != "upstream timeout" {
		t.Errorf("raw body: got %q", got)
	}
	if got := (&APIError{Status: 409, Body: `{"code":"conflict","message":"already running","request_id":"r"}`}).Error(); got != "API error 409: already running" {
		t.Errorf("APIError: got %q", got)
	}
}

func TestClient_TagFile(t *testing.T) {
	var gotMethod, gotPath string
	var gotBody map[string][]string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/api/v1/tags" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`[{"id":"` + idScan + `","name":"invoices","color":"#fff"}]`))
			return
		}
		gotMethod, gotPath = r.Method, r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()
	tag, err := newClient(t, server.URL).TagFile(context.Background(), scanID, "invoices")
	if err != nil {
		t.Fatal(err)
	}
	if tag.Name != "invoices" || tag.Id.String() != idScan {
		t.Errorf("tag = %+v", tag)
	}
	if gotMethod != http.MethodPost || gotPath != "/api/v1/entries/"+idScan+"/tags" {
		t.Errorf("got %s %s", gotMethod, gotPath)
	}
	if len(gotBody["tags"]) != 1 || gotBody["tags"][0] != "invoices" {
		t.Errorf("body = %v", gotBody)
	}
}

func TestParseID(t *testing.T) {
	if _, err := ParseID("scan id", idScan); err != nil {
		t.Errorf("valid uuid rejected: %v", err)
	}
	if _, err := ParseID("scan id", "abc"); err == nil {
		t.Error("non-uuid accepted")
	}
}
