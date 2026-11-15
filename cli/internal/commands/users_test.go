package commands

import (
	"bytes"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/cli/internal/client"
)

const testUserJSON = `{"id":"11111111-1111-1111-1111-111111111111","username":"alice","email":null,"role":%q,"role_pinned":%t,"auth_provider":"oidc","created_at":"2026-01-01T00:00:00Z","last_login_at":null}`

// `users set-role <username> admin --unpin` resolves the name through the list
// and PATCHes the id with role and role_pinned=false.
func TestUsersSetRoleByUsername(t *testing.T) {
	var patched string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/users":
			_, _ = w.Write([]byte("[" + fmt.Sprintf(testUserJSON, "viewer", false) + "]"))
		case r.Method == http.MethodPatch && r.URL.Path == "/api/v1/users/11111111-1111-1111-1111-111111111111":
			b, _ := io.ReadAll(r.Body)
			patched = string(b)
			_, _ = w.Write([]byte(fmt.Sprintf(testUserJSON, "admin", false)))
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
	cmd := NewUsersCmd(c)
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetArgs([]string{"set-role", "alice", "admin", "--unpin"})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(patched, `"role":"admin"`) || !strings.Contains(patched, `"role_pinned":false`) {
		t.Errorf("unexpected PATCH body %s", patched)
	}
	if !strings.Contains(out.String(), "alice is now admin") {
		t.Errorf("unexpected output %q", out.String())
	}
}

func TestUsersSetRoleRejectsBadRole(t *testing.T) {
	c, _ := client.New("http://127.0.0.1:1", "key")
	cmd := NewUsersCmd(c)
	cmd.SetArgs([]string{"set-role", "alice", "root"})
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected error")
	}
}
