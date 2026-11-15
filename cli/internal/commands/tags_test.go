package commands

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/cli/internal/client"
)

// `tag add` applies the tag (204, no body) and then prints the tag's id, which
// it looks up in the catalogue.
func TestTagAddPrintsTagID(t *testing.T) {
	const fileID = "11111111-1111-1111-1111-111111111111"
	const tagID = "22222222-2222-2222-2222-222222222222"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/api/v1/entries/"+fileID+"/tags":
			w.WriteHeader(http.StatusNoContent)
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/tags":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`[{"id":"` + tagID + `","name":"other","color":"#000000"},{"id":"` + tagID + `","name":"urgent","color":"#ff0000"}]`))
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	c, err := client.New(srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	cmd := NewTagCmd(c)
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"add", "--file", fileID, "--tag", "urgent"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("tag add: %v", err)
	}
	want := `Tagged file ` + fileID + ` with tag "urgent" (id: ` + tagID + `)`
	if !strings.Contains(out.String(), want) {
		t.Fatalf("output %q does not contain %q", out.String(), want)
	}
}
