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
	appID = "11111111-1111-1111-1111-111111111111"
	nasID = "22222222-2222-2222-2222-222222222222"
	arcID = "33333333-3333-3333-3333-333333333333"
)

type mirrorSrv struct {
	patch map[string]any
	srv   *httptest.Server
}

func newMirrorSrv(t *testing.T) *mirrorSrv {
	m := &mirrorSrv{}
	existing := `[{"source_id":"` + nasID + `","remote_prefix":"/old","local_prefix":"/old"},{"source_id":"` + arcID + `","remote_prefix":"/a","local_prefix":"/arch"}]`
	m.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/sources":
			_, _ = w.Write([]byte(`[{"id":"` + appID + `","name":"photos","type":"immich"},{"id":"` + nasID + `","name":"nas","type":"local"},{"id":"` + arcID + `","name":"archive","type":"local"}]`))
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/sources/"+appID:
			_, _ = w.Write([]byte(`{"id":"` + appID + `","name":"photos","type":"immich","connection_config":{"url":"http://x","mirrors":` + existing + `}}`))
		case r.Method == http.MethodPatch && r.URL.Path == "/api/v1/sources/"+appID:
			b, _ := io.ReadAll(r.Body)
			_ = json.Unmarshal(b, &m.patch)
			cc := m.patch["connection_config"].(map[string]any)
			out, _ := json.Marshal(map[string]any{"id": appID, "name": "photos", "type": "immich", "connection_config": cc})
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

func TestMirrorSetReplacesBackingMapping(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "set", "photos", "--backing", "nas", "--remote-prefix", "/new", "--local-prefix", "/mnt/new")
	cc := m.patch["connection_config"].(map[string]any)
	if len(cc) != 1 {
		t.Fatalf("connection_config keys != {mirrors}: %v", cc)
	}
	list := cc["mirrors"].([]any)
	if len(list) != 2 {
		t.Fatalf("want 2 mappings, got %v", list)
	}
	first := list[0].(map[string]any)
	if first["source_id"] != nasID || first["remote_prefix"] != "/new" || first["local_prefix"] != "/mnt/new" {
		t.Fatalf("mapping not replaced in place: %v", first)
	}
	if list[1].(map[string]any)["local_prefix"] != "/arch" {
		t.Fatalf("other backing lost: %v", list[1])
	}
	if !strings.Contains(out, `Mirror set: photos → nas (remote "/new" → local "/mnt/new")`) {
		t.Fatalf("output %q", out)
	}
}

func TestMirrorSetMissingMappingInResponseErrors(t *testing.T) {
	m := newMirrorSrv(t)
	m.srv.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/sources":
			_, _ = w.Write([]byte(`[{"id":"` + appID + `","name":"photos","type":"immich"},{"id":"` + nasID + `","name":"nas","type":"local"}]`))
		case r.Method == http.MethodGet:
			_, _ = w.Write([]byte(`{"id":"` + appID + `","name":"photos","type":"immich","connection_config":{}}`))
		default:
			_, _ = w.Write([]byte(`{"id":"` + appID + `","name":"photos","type":"immich","connection_config":{"mirrors":[]}}`))
		}
	})
	c, err := client.New(m.srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	cmd := NewSourcesCmd(c)
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"mirror", "set", "photos", "--backing", "nas", "--local-prefix", "/x"})
	if err = cmd.Execute(); err == nil {
		t.Fatal("expected error when response lacks mapping")
	}
}

func TestMirrorClearAll(t *testing.T) {
	m := newMirrorSrv(t)
	runSources(t, m, "mirror", "clear", "photos")
	b, _ := json.Marshal(m.patch)
	if string(b) != `{"connection_config":{"mirrors":[]}}` {
		t.Fatalf("body %s", b)
	}
}

func TestMirrorShowPrintsRolesPerBacking(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "show", "photos")
	for _, w := range []string{"BACKING", "nas:", "archive:", "local", "/old", "ROLE", "original", "unlinked"} {
		if !strings.Contains(out, w) {
			t.Fatalf("output missing %q:\n%s", w, out)
		}
	}
}

func TestMirrorClearOneBacking(t *testing.T) {
	m := newMirrorSrv(t)
	out := runSources(t, m, "mirror", "clear", "photos", "--backing", "nas")
	list := m.patch["connection_config"].(map[string]any)["mirrors"].([]any)
	if len(list) != 1 || list[0].(map[string]any)["source_id"] != arcID {
		t.Fatalf("want only archive mapping, got %v", list)
	}
	if !strings.Contains(out, "photos → nas") {
		t.Fatalf("output %q", out)
	}
}

func TestMirrorClearBackingMissingErrors(t *testing.T) {
	m := newMirrorSrv(t)
	c, err := client.New(m.srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	cmd := NewSourcesCmd(c)
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	cmd.SetArgs([]string{"mirror", "clear", "photos", "--backing", "photos"})
	if err = cmd.Execute(); err == nil {
		t.Fatal("expected error for missing mapping")
	}
	if m.patch != nil {
		t.Fatalf("unexpected PATCH %v", m.patch)
	}
}
