package extract

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"sort"
	"sync"
	"testing"

	"github.com/akashic-project/akashic/scanner/internal/walker"
	"github.com/akashic-project/akashic/scanner/pkg/models"
)

// fakeConn is a minimal connector.Connector for pool tests: ReadFile
// serves canned content; one path can be made to error.
type fakeConn struct {
	typ      string
	files    map[string][]byte
	failPath string
}

func (f *fakeConn) Connect(context.Context) error { return nil }
func (f *fakeConn) Walk(context.Context, string, []string, bool, bool, func(*models.EntryRecord) error) (walker.WalkStats, error) {
	return walker.WalkStats{}, nil
}
func (f *fakeConn) ReadFile(_ context.Context, path string) (io.ReadCloser, error) {
	if path == f.failPath {
		return nil, fmt.Errorf("simulated read error for %s", path)
	}
	content, ok := f.files[path]
	if !ok {
		return nil, fmt.Errorf("not found: %s", path)
	}
	return io.NopCloser(bytes.NewReader(content)), nil
}
func (f *fakeConn) Delete(context.Context, string) error { return nil }
func (f *fakeConn) Close() error                         { return nil }
func (f *fakeConn) Type() string                         { return f.typ }

func discardLog(string, ...any) {}

func TestClampWorkers(t *testing.T) {
	if got := clampWorkers("smb", 8); got != 2 {
		t.Errorf("smb clamp: got %d, want 2", got)
	}
	if got := clampWorkers("local", 8); got != 8 {
		t.Errorf("local: got %d, want 8", got)
	}
	if got := clampWorkers("local", 0); got != DefaultWorkers {
		t.Errorf("default: got %d, want %d", got, DefaultWorkers)
	}
	if got := clampWorkers("smb", 1); got != 1 {
		t.Errorf("smb already low: got %d, want 1", got)
	}
}

func TestPool_DrainsAllJobs(t *testing.T) {
	conn := &fakeConn{
		typ: "local",
		files: map[string][]byte{
			"/a.txt": []byte("alpha"),
			"/b.txt": []byte("bravo"),
			"/c.txt": []byte("charlie"),
		},
	}
	var mu sync.Mutex
	var got []ContentRecord
	sink := func(rec ContentRecord) {
		mu.Lock()
		got = append(got, rec)
		mu.Unlock()
	}
	pool := NewPool(conn, NewExtractor(""), 2, sink, discardLog)
	for path := range conn.files {
		pool.Submit(Job{Path: path, MimeType: "text/plain", Size: 5})
	}
	pool.Close()

	if len(got) != 3 {
		t.Fatalf("sink received %d records, want 3", len(got))
	}
	sort.Slice(got, func(i, j int) bool { return got[i].Path < got[j].Path })
	if got[0].ContentText != "alpha" || got[2].ContentText != "charlie" {
		t.Errorf("unexpected content: %+v", got)
	}
	extracted, failures := pool.Stats()
	if extracted != 3 || failures != 0 {
		t.Errorf("stats: extracted=%d failures=%d, want 3/0", extracted, failures)
	}
}

func TestPool_SwallowsReadErrors(t *testing.T) {
	conn := &fakeConn{
		typ:      "local",
		failPath: "/bad.txt",
		files: map[string][]byte{
			"/good.txt": []byte("ok"),
		},
	}
	var mu sync.Mutex
	var got []ContentRecord
	sink := func(rec ContentRecord) {
		mu.Lock()
		got = append(got, rec)
		mu.Unlock()
	}
	pool := NewPool(conn, NewExtractor(""), 2, sink, discardLog)
	pool.Submit(Job{Path: "/bad.txt", MimeType: "text/plain", Size: 2})
	pool.Submit(Job{Path: "/good.txt", MimeType: "text/plain", Size: 2})
	pool.Close()

	// The failing job must not stop the good one.
	if len(got) != 1 || got[0].Path != "/good.txt" {
		t.Fatalf("sink = %+v, want one record for /good.txt", got)
	}
	extracted, failures := pool.Stats()
	if extracted != 1 || failures != 1 {
		t.Errorf("stats: extracted=%d failures=%d, want 1/1", extracted, failures)
	}
}

func TestPool_CloseWaitsForInFlight(t *testing.T) {
	conn := &fakeConn{typ: "local", files: map[string][]byte{}}
	for i := 0; i < 50; i++ {
		conn.files[fmt.Sprintf("/f%d.txt", i)] = []byte("data")
	}
	var n int
	var mu sync.Mutex
	sink := func(ContentRecord) {
		mu.Lock()
		n++
		mu.Unlock()
	}
	pool := NewPool(conn, NewExtractor(""), 4, sink, discardLog)
	for path := range conn.files {
		pool.Submit(Job{Path: path, MimeType: "text/plain", Size: 4})
	}
	pool.Close() // must block until every worker has drained
	if n != 50 {
		t.Errorf("Close returned with %d/50 jobs processed", n)
	}
}
