// gzip request body (v0.29.2 Part B).
//
// SendBatch gzips the JSON body when over 1 KB and sets
// Content-Encoding: gzip. Real scan batches are 50 KB–1 MB and
// compress 80–90% (JSON is mostly repeated keys + paths + ASCII).
// Pre-fix the body went out plain; on a remote scanner with a slow
// uplink that was the dominant per-batch wall-clock cost.
package client

import (
	"compress/gzip"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/akashic-project/akashic/scanner/pkg/models"
)

func TestSendBatch_GzipsLargeBody(t *testing.T) {
	// Build a batch with enough entries that the JSON marshaled body
	// is well over the 1 KB threshold. ~50 entries of ~50 bytes each
	// puts us comfortably above.
	entries := make([]models.EntryRecord, 50)
	for i := range entries {
		entries[i] = models.EntryRecord{
			Path: "/some/longer/path/to/keep/each/entry/over/40/bytes/" + strings.Repeat("a", 32),
			Kind: "file",
		}
	}
	batch := models.ScanBatch{
		SourceID: "src", ScanID: "scan", Entries: entries,
	}

	var sawEncoding string
	var decompressed []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawEncoding = r.Header.Get("Content-Encoding")
		if sawEncoding == "gzip" {
			gr, err := gzip.NewReader(r.Body)
			if err != nil {
				t.Errorf("server gzip.NewReader: %v", err)
				w.WriteHeader(500)
				return
			}
			defer gr.Close()
			decompressed, _ = io.ReadAll(gr)
		} else {
			decompressed, _ = io.ReadAll(r.Body)
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "test-jwt")
	if err := c.SendBatch(context.Background(), batch); err != nil {
		t.Fatalf("SendBatch: %v", err)
	}
	if sawEncoding != "gzip" {
		t.Fatalf("expected Content-Encoding: gzip, got %q", sawEncoding)
	}
	// Decompressed body must round-trip to the same JSON we'd marshal
	// directly — proves the API server-side gzip middleware will see
	// a valid plain-JSON body after decoding.
	want, _ := json.Marshal(batch)
	if string(decompressed) != string(want) {
		t.Errorf("decompressed body mismatch:\ngot  %d bytes\nwant %d bytes",
			len(decompressed), len(want))
	}
}

func TestSendBatch_PlainBelowGzipThreshold(t *testing.T) {
	// A trivially small batch (one entry) is well under 1 KB and
	// should NOT be gzipped — compression overhead would exceed the
	// bandwidth savings.
	batch := models.ScanBatch{
		SourceID: "s", ScanID: "x",
		Entries: []models.EntryRecord{{Path: "/a", Kind: "file"}},
	}

	var sawEncoding string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawEncoding = r.Header.Get("Content-Encoding")
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	c := New(srv.URL, "test-jwt")
	if err := c.SendBatch(context.Background(), batch); err != nil {
		t.Fatalf("SendBatch: %v", err)
	}
	if sawEncoding != "" {
		t.Errorf("expected no Content-Encoding for tiny body; got %q", sawEncoding)
	}
}
