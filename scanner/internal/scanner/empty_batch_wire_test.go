// Empty-batch JSON wire shape (v0.29.6).
//
// Pre-fix: scanner.go initialised `var batch []EntryRecord` (nil
// slice) and emitted a final batch with `Entries: batch` when the
// walker produced zero entries. Go's encoding/json marshals a nil
// slice to `null`, which the API's required `list[EntryIn]` Pydantic
// field rejects with 422 — the exact error the user reported on the
// production scanner.
//
// This test drives Run() with a stub Walk that yields nothing and
// asserts the wire body contains `"entries":[]`, not `"entries":null`.
package scanner

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/client"
	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// emptyWalkConn is a Connector that emits zero entries.
type emptyWalkConn struct{}

func (emptyWalkConn) Connect(_ context.Context) error { return nil }
func (emptyWalkConn) Close() error                    { return nil }
func (emptyWalkConn) Type() string                    { return "empty-stub" }
func (emptyWalkConn) Delete(_ context.Context, _ string) error {
	return fmt.Errorf("not supported")
}
func (emptyWalkConn) ReadFile(_ context.Context, _ string) (io.ReadCloser, error) {
	return nil, fmt.Errorf("not supported")
}
func (emptyWalkConn) Walk(
	_ context.Context, _ string, _ []string, _ bool, _ bool,
	_ func(*models.EntryRecord) error,
) (walker.WalkStats, error) {
	return walker.WalkStats{}, nil
}

func TestRun_EmptyWalk_SendsEntriesAsEmptyList(t *testing.T) {
	var seenBody []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Mirror the production middleware: decode gzip when the
		// header is set, so the assertion sees plain JSON regardless
		// of whether the client compressed.
		var body io.Reader = r.Body
		if r.Header.Get("Content-Encoding") == "gzip" {
			gz, err := gzip.NewReader(r.Body)
			if err != nil {
				w.WriteHeader(400)
				return
			}
			defer gz.Close()
			body = gz
		}
		seenBody, _ = io.ReadAll(body)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "k")
	s := New(c, emptyWalkConn{}, Options{
		SourceID: "src", ScanID: "scan-empty",
		Root: "/", BatchSize: 10,
	})
	if _, err := s.Run(context.Background()); err != nil {
		t.Fatalf("Run failed: %v", err)
	}

	if len(seenBody) == 0 {
		t.Fatal("server saw no batch POST")
	}
	body := string(seenBody)
	// The load-bearing assertion: never `"entries":null`.
	if strings.Contains(body, `"entries":null`) {
		t.Errorf("wire body contains entries:null — the v0.29.6 bug\nbody:\n%s",
			body)
	}
	if !strings.Contains(body, `"entries":[]`) {
		t.Errorf("expected entries:[] in wire body; got:\n%s", body)
	}
	if !strings.Contains(body, `"is_final":true`) {
		t.Errorf("expected is_final:true in wire body; got:\n%s", body)
	}
}

func TestRun_EmptyWalk_RoundTripsThroughPydanticShape(t *testing.T) {
	// Belt: minimally validate that the JSON the scanner produces
	// parses as a JSON object with `entries` as an array. Pydantic's
	// `list[EntryIn]` would accept any JSON list, including empty.
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body io.Reader = r.Body
		if r.Header.Get("Content-Encoding") == "gzip" {
			gz, _ := gzip.NewReader(r.Body)
			defer gz.Close()
			body = gz
		}
		raw, _ := io.ReadAll(body)
		got = map[string]any{}
		_ = json.Unmarshal(raw, &got)
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := client.New(srv.URL, "k")
	s := New(c, emptyWalkConn{}, Options{
		SourceID: "src", ScanID: "scan-rt",
		Root: "/", BatchSize: 10,
	})
	if _, err := s.Run(context.Background()); err != nil {
		t.Fatalf("Run failed: %v", err)
	}
	entries, ok := got["entries"]
	if !ok {
		t.Fatalf("entries field missing from JSON body: %v", got)
	}
	if entries == nil {
		t.Errorf("entries decoded to nil — Pydantic would reject as 422")
	}
	if _, ok := entries.([]any); !ok {
		t.Errorf("entries is not a JSON array: %T %v", entries, entries)
	}
}

