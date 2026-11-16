package commands

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/cli/internal/client"
)

const (
	backID  = "11111111-1111-1111-1111-111111111111"
	immID   = "22222222-2222-2222-2222-222222222222"
	paperID = "33333333-3333-3333-3333-333333333333"
)

type mirrorSrv struct {
	patch map[string]any
	srv   *httptest.Server
}

func newMirrorSrv(t *testing.T) *mirrorSrv {
	m := &mirrorSrv{}
	existing := `[{"source_id":"` + paperID + `","remote_prefix":"/p","local_prefix":"/docs"},{"source_id":"` + immID + `","remote_prefix":"/old","local_prefix":"/old"}]`
	m.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/sources":
			_, _ = w.Write([]byte(`[{"id":"` + backID + `","name":"nas","type":"local"},{"id":"` + immID + `","name":"photos","type":"immich"},{"id":"` + paperID + `","name":"docs","type":"paperless"}]`))
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/sources/"+backID:
			_, _ = w.Write([]byte(`{"id":"` + backID + `","name":"nas","type":"local","connection_config":{"path":"/x","mirror_of":` + existing + `}}`))
		case r.Method == http.MethodPatch && r.URL.Path == "/api/v1/sources/"+backID:
			b, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(b, &m.patch)
			cc := m.patch["connection_config"].(map[string]any)
			out, _ := json.Marshal(map[string]any{"id": backID, "name": "nas", "type": "local", "connection_config": cc})
			_, _ = w.Write(out)
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/analytics/storage-by-link-role":
			_, _ = w.Write([]byte(`[{"link_role":"original","count":3,"total_size":300},{"link_role":"thumbnail","count":0,"total_size":0},{"link_role":"unlinked","count":2,"total_size":20}]`))
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(m.srv.Close)
	return m
}

func runSources(t *testing.T, m *mirrorSrv, args ...string) string {
	c, err := client.New(m.srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	cmd := NewSourcesCmd(c)
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetArgs(args)
	if err := cmd.Execute(); err != nil {
		t.Fatalf("%v: %v", args, err)
	}
	return out.String()
}

func TestMirrorSetReplacesTargetMapping(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "set", "nas", "--target", "photos", "--remote-prefix", "/new", "--local-prefix", "/mnt/new")
	cc := m.patch["connection_config"].(map[string]any)
	if len(cc) != 1 {
		t.Fatalf("connection_config keys != {mirror_of}: %v", cc)
	}
	list := cc["mirror_of"].([]any)
	if len(list) != 2 {
		t.Fatalf("want 2 mappings, got %v", list)
	}
	got := map[string]map[string]any{}
	for _, e := range list {
		em := e.(map[string]any)
		got[em["source_id"].(string)] = em
	}
	if got[immID]["remote_prefix"] != "/new" || got[immID]["local_prefix"] != "/mnt/new" {
		t.Fatalf("target mapping not replaced: %v", got[immID])
	}
	if got[paperID]["local_prefix"] != "/docs" {
		t.Fatalf("other mapping lost: %v", got[paperID])
	}
	if !strings.Contains(out, `Mirror set: nas → photos (remote "/new" → local "/mnt/new")`) {
		t.Fatalf("output %q", out)
	}
}

func TestMirrorClearAll(t *testing.T) {
	m := newMirrorSrv(t)
	runSources(t, m, "mirror", "clear", "nas")
	b, _ := json.Marshal(m.patch)
	if string(b) != `{"connection_config":{"mirror_of":[]}}` {
		t.Fatalf("body %s", b)
	}
}

func TestMirrorShowPrintsRoles(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "show", "nas")
	for _, w := range []string{"TARGET", "photos", "immich", "/old", "ROLE", "original", "unlinked"} {
		if !strings.Contains(out, w) {
			t.Fatalf("output missing %q:\n%s", w, out)
		}
	}
}

func TestMirrorClearTargetKeepsOthers(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "clear", "nas", "--target", "photos")
	list := m.patch["connection_config"].(map[string]any)["mirror_of"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["source_id"] != paperID {
		t.Fatalf("want only paperless mapping, got %v", list)
	}
	if !strings.Contains(out, "nas → photos") {
		t.Fatalf("output %q", out)
	}
}

func TestMirrorClearTargetMissingErrors(t *testing.T) {
	m := newMirrorSrv(t)
	c, _ := client.New(m.srv.URL, "key")
	cmd := NewSourcesCmd(c)
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"mirror", "clear", "nas", "--target", "nas"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error for missing mapping")
	}
	if m.patch != nil {
		t.Fatalf("unexpected PATCH %v", m.patch)
	}
}
