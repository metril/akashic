package commands

import (
	"bytes"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/akashic-project/akashic/cli/internal/client"
)

func accessTestCmd(t *testing.T, h http.HandlerFunc, args ...string) (string, error) {
	t.Helper()
	srv := httptest.NewServer(h)
	defer srv.Close()
	c, err := client.New(srv.URL, "key")
	if err != nil {
		t.Fatal(err)
	}
	cmd := NewAccessCmd(c)
	var out bytes.Buffer
	cmd.SetOut(&out)
	cmd.SetErr(&out)
	cmd.SetArgs(args)
	err = cmd.Execute()
	return out.String(), err
}

// fastPoll shortens the sync poll interval for one test and restores it.
func fastPoll(t *testing.T) {
	t.Helper()
	old := syncPollInterval
	syncPollInterval = time.Millisecond
	t.Cleanup(func() { syncPollInterval = old })
}

func identityJSON(id, quality string) string {
	return `{"id":"` + id + `","kind":"person","display_name":"Alice Doe","username":"alice","backends":[{"backend":"ad","ref":"g1"},{"backend":"authentik","ref":"7"}],"match_quality":"` + quality + `","possible_match_of":[],"has_fs_identity":true}`
}

func TestAccessWhoPrintsBackendsAndPossible(t *testing.T) {
	out, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/access/identities" || r.URL.Query().Get("q") != "alice" {
			t.Errorf("unexpected request %s", r.URL)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[` + identityJSON("sid:S1", "exact") + `,` + identityJSON("authentik:9", "possible") + `],"backends_status":[]}`))
	}, "who", "alice")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"sid:S1", "Alice Doe", "ad,authentik", "possible"} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
}

func TestAccessShowByQueryUniqueMatch(t *testing.T) {
	out, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/access/identities":
			_, _ = w.Write([]byte(`{"results":[` + identityJSON("sid:S1", "exact") + `,` + identityJSON("authentik:9", "possible") + `],"backends_status":[]}`))
		case "/api/v1/access":
			if r.URL.Query().Get("identity") != "sid:S1" || r.URL.Query().Get("right") != "read" {
				t.Errorf("unexpected query %s", r.URL.RawQuery)
			}
			if !strings.Contains(r.URL.RawQuery, "identity=sid%3AS1") {
				t.Errorf("identity not URL-encoded: %s", r.URL.RawQuery)
			}
			_, _ = w.Write([]byte(`{"principal":{"token":"sid:S1","type":"sid"},"right":"read","summary":{"file_count":42,"source_count":1,"total_size_bytes":4096},"by_source":[{"source_id":"s","source_name":"nas","file_count":42}],"sample":[{"id":"e1","source_id":"s","path":"/a/denied.txt","filename":"denied.txt","has_deny_ace":true},{"id":"e2","source_id":"s","path":"/a/ok.txt","filename":"ok.txt","has_deny_ace":false}],"limit":20,"next_cursor":null,"identity":` + identityJSON("sid:S1", "exact") + `,"tokens_used":[{"token":"sid:S-1-5-21-1","origin":"ad","name":"Alice"},{"token":"*","origin":"builtin","name":null}],"expansion":{"status":"cached","cached_at":"2026-11-16T10:00:00Z","detail":"group read failed","missing_identity_kinds":[]},"warnings":["Deny ACEs applied through groups are not evaluated."]}`))
		default:
			t.Errorf("unexpected request %s", r.URL)
		}
	}, "show", "alice")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"42 in 1 sources, 4.0 KiB (4096 bytes)", "sid:S-1-5-21-1  [ad]  Alice", "[builtin]", "Group expansion: cached (as of 2026-11-16T10:00:00Z): group read failed", "/a/denied.txt  [deny ACE]", "Warning: Deny ACEs applied through groups are not evaluated."} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "/a/ok.txt  [deny") {
		t.Errorf("row without deny ACE was flagged:\n%s", out)
	}
}

func TestAccessShowAmbiguous(t *testing.T) {
	_, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[` + identityJSON("sid:S1", "exact") + `,` + identityJSON("sid:S2", "exact") + `],"backends_status":[]}`))
	}, "show", "alice")
	var ee *ExitErr
	if !errors.As(err, &ee) || ee.Code != ExitUserErr {
		t.Fatalf("want ExitUserErr, got %v", err)
	}
	if !strings.Contains(err.Error(), "sid:S1") || !strings.Contains(err.Error(), "sid:S2") {
		t.Errorf("message lacks candidate ids: %v", err)
	}
}

func TestAccessShowNoMatch(t *testing.T) {
	_, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"results":[` + identityJSON("authentik:9", "possible") + `],"backends_status":[]}`))
	}, "show", "alice")
	var ee *ExitErr
	if !errors.As(err, &ee) || ee.Code != ExitUserErr || !strings.Contains(err.Error(), "no identity matches") {
		t.Fatalf("want ExitUserErr no identity matches, got %v", err)
	}
}

func TestAccessSyncConflict(t *testing.T) {
	_, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_, _ = w.Write([]byte(`{"code":"conflict","message":"sync in progress","request_id":"r"}`))
	}, "sync")
	var ee *ExitErr
	if !errors.As(err, &ee) || ee.Code != ExitUserErr || !strings.Contains(err.Error(), "already running") {
		t.Fatalf("want ExitUserErr already running, got %v", err)
	}
}

const syncRunJSON = `{"id":"11111111-1111-1111-1111-111111111111","backend":"ad","status":%q,"trigger":"manual","started_at":"2026-11-16T10:00:00Z","finished_at":null,"counts":{},"error":%s}`

func syncStatusJSON(running bool, status, errJSON string) string {
	run := strings.NewReplacer("%q", `"`+status+`"`, "%s", errJSON).Replace(syncRunJSON)
	r := "false"
	if running {
		r = "true"
	}
	return `{"backends":[{"backend":"ad","configured":true,"running":` + r + `,"last_run":` + run + `,"last_success_at":null}],"interval_minutes":60,"next_run_at":null}`
}

func syncHandler(t *testing.T, polls *atomic.Int32, final string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/access/directory/sync":
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"runs":[` + strings.NewReplacer("%q", `"running"`, "%s", "null").Replace(syncRunJSON) + `]}`))
		case "/api/v1/access/directory/status":
			if polls.Add(1) == 1 {
				_, _ = w.Write([]byte(syncStatusJSON(true, "running", "null")))
				return
			}
			_, _ = w.Write([]byte(final))
		default:
			t.Errorf("unexpected request %s", r.URL)
		}
	}
}

func TestAccessSyncWaitFailsOnFailedRun(t *testing.T) {
	fastPoll(t)
	var polls atomic.Int32
	_, err := accessTestCmd(t, syncHandler(t, &polls, syncStatusJSON(false, "failed", `"bind failed"`)), "sync", "--wait")
	if err == nil || !strings.Contains(err.Error(), "bind failed") {
		t.Fatalf("want failed-run error, got %v", err)
	}
	if polls.Load() < 2 {
		t.Errorf("expected polling until the run stopped, polls=%d", polls.Load())
	}
}

func TestAccessSyncWaitSucceeds(t *testing.T) {
	fastPoll(t)
	var polls atomic.Int32
	out, err := accessTestCmd(t, syncHandler(t, &polls, syncStatusJSON(false, "succeeded", "null")), "sync", "--wait")
	if err != nil || !strings.Contains(out, "sync finished") {
		t.Fatalf("err=%v out=%s", err, out)
	}
}

func TestAccessSyncStatusPrintsSchedule(t *testing.T) {
	out, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(syncStatusJSON(false, "succeeded", "null")))
	}, "sync-status")
	if err != nil || !strings.Contains(out, "every 60 min") || !strings.Contains(out, "ad") {
		t.Fatalf("err=%v out=%s", err, out)
	}
}

func TestAccessShowDNIdentityIsURLEncoded(t *testing.T) {
	var raw string
	_, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		raw = r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"principal":{"token":"x","type":"sid"},"right":"read","summary":{"file_count":0,"source_count":0,"total_size_bytes":0},"by_source":[],"sample":[],"limit":20,"next_cursor":null,"warnings":[]}`))
	}, "show", "dn:CN=Alice,OU=Eng,DC=x")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(raw, "identity=dn%3ACN%3DAlice%2COU%3DEng%2CDC%3Dx") {
		t.Errorf("dn id not URL-encoded: %s", raw)
	}
}

func TestHumanSize(t *testing.T) {
	for in, want := range map[int]string{0: "0 B", 1023: "1023 B", 1024: "1.0 KiB", 4096: "4.0 KiB", 1536 * 1024: "1.5 MiB", 3 << 30: "3.0 GiB"} {
		if got := humanSize(in); got != want {
			t.Errorf("humanSize(%d)=%q want %q", in, got, want)
		}
	}
}

func TestAccessWhoShowsPossibleMatchIdsAndBackendProblems(t *testing.T) {
	out, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		poss := strings.Replace(identityJSON("authentik:9", "possible"), `"possible_match_of":[]`, `"possible_match_of":["sid:S1","email:a@x.io"]`, 1)
		_, _ = w.Write([]byte(`{"results":[` + poss + `],"backends_status":[{"backend":"ad","status":"ok","detail":null},{"backend":"authentik","status":"unavailable","detail":"HTTP 502 on /api"},{"backend":"akashic","status":"ok","detail":null}]}`))
	}, "who", "alice")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"possible match of sid:S1,email:a@x.io", "authentik: unavailable (HTTP 502 on /api)"} {
		if !strings.Contains(out, want) {
			t.Errorf("output missing %q:\n%s", want, out)
		}
	}
	if strings.Contains(out, "ad: ok") || strings.Contains(out, "akashic: ok") {
		t.Errorf("ok backends must not be listed:\n%s", out)
	}
}

func TestAccessSyncWaitIgnoresStaleFailedRunOfOtherBackend(t *testing.T) {
	fastPoll(t)
	other := strings.NewReplacer("%q", `"failed"`, "%s", `"old failure"`).Replace(
		strings.Replace(syncRunJSON, "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", 1))
	ok := strings.NewReplacer("%q", `"succeeded"`, "%s", "null").Replace(syncRunJSON)
	final := `{"backends":[` +
		`{"backend":"ad","configured":true,"running":false,"last_run":` + ok + `,"last_success_at":null},` +
		`{"backend":"authentik","configured":true,"running":false,"last_run":` + other + `,"last_success_at":null}` +
		`],"interval_minutes":60,"next_run_at":null}`
	var polls atomic.Int32
	out, err := accessTestCmd(t, syncHandler(t, &polls, final), "sync", "--wait")
	if err != nil || !strings.Contains(out, "sync finished") {
		t.Fatalf("stale failed run of another backend must not fail the wait: err=%v out=%s", err, out)
	}
}

func TestAccessSyncWaitDeadlineDuringStatusCall(t *testing.T) {
	_, err := accessTestCmd(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/v1/access/directory/sync" {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"runs":[` + strings.NewReplacer("%q", `"running"`, "%s", "null").Replace(syncRunJSON) + `]}`))
			return
		}
		<-r.Context().Done() // status call hangs until the client gives up
	}, "sync", "--wait", "--timeout", "50ms")
	var ee *ExitErr
	if !errors.As(err, &ee) || !strings.Contains(err.Error(), "timed out waiting for directory sync") {
		t.Fatalf("want timed-out message, got %v", err)
	}
	if strings.Contains(err.Error(), "context deadline exceeded") {
		t.Errorf("raw context error leaked: %v", err)
	}
}
