package extract

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestIsEligible(t *testing.T) {
	cases := []struct {
		name string
		mime string
		size int64
		want bool
	}{
		{"plain text", "text/plain", 100, true},
		{"text/ prefix (markdown)", "text/markdown", 100, true},
		{"json", "application/json", 100, true},
		{"pdf (tika)", "application/pdf", 100, true},
		{"docx (tika)", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 100, true},
		{"image — not eligible", "image/png", 100, false},
		{"video — not eligible", "video/mp4", 100, false},
		{"octet-stream — not eligible", "application/octet-stream", 100, false},
		{"empty mime — not eligible", "", 100, false},
		{"oversized pdf — not eligible", "application/pdf", MaxExtractionSize + 1, false},
		{"at-cap pdf — eligible", "application/pdf", MaxExtractionSize, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsEligible(tc.mime, tc.size); got != tc.want {
				t.Errorf("IsEligible(%q, %d) = %v, want %v", tc.mime, tc.size, got, tc.want)
			}
		})
	}
}

func TestExtractPlain(t *testing.T) {
	if got := ExtractPlain([]byte("hello world")); got != "hello world" {
		t.Errorf("utf-8: got %q", got)
	}
	if got := ExtractPlain([]byte("  trimmed  ")); got != "trimmed" {
		t.Errorf("trim: got %q", got)
	}
	if got := ExtractPlain([]byte("")); got != "" {
		t.Errorf("empty: got %q", got)
	}
	if got := ExtractPlain([]byte("   \n\t  ")); got != "" {
		t.Errorf("whitespace-only: got %q", got)
	}
	// Invalid UTF-8 (0xFF) → latin-1 fallback, must not panic or drop.
	got := ExtractPlain([]byte{0x68, 0x69, 0xff})
	if !strings.HasPrefix(got, "hi") {
		t.Errorf("latin-1 fallback: got %q, want prefix 'hi'", got)
	}
}

func TestExtractTika(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPut {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("  extracted document text  "))
	}))
	defer srv.Close()

	ex := NewExtractor(srv.URL)
	got, err := ex.ExtractTika(context.Background(), []byte("%PDF-fake"), "application/pdf")
	if err != nil {
		t.Fatalf("ExtractTika: %v", err)
	}
	if got != "extracted document text" {
		t.Errorf("got %q, want trimmed 'extracted document text'", got)
	}
}

func TestExtractTika_ServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	ex := NewExtractor(srv.URL)
	if _, err := ex.ExtractTika(context.Background(), []byte("x"), "application/pdf"); err == nil {
		t.Error("expected error on Tika 500")
	}
}

func TestExtractTika_Disabled(t *testing.T) {
	// Empty tikaURL → document extraction is a no-op, no error.
	ex := NewExtractor("")
	if ex.TikaEnabled() {
		t.Error("TikaEnabled should be false for empty URL")
	}
	got, err := ex.ExtractTika(context.Background(), []byte("x"), "application/pdf")
	if err != nil || got != "" {
		t.Errorf("disabled extractor: got (%q, %v), want (\"\", nil)", got, err)
	}
}

func TestExtract_Dispatch(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("tika-text"))
	}))
	defer srv.Close()
	ex := NewExtractor(srv.URL)

	// Plain text — decoded natively, never hits Tika.
	got, err := ex.Extract(context.Background(), []byte("plain"), "text/plain")
	if err != nil || got != "plain" {
		t.Errorf("plain dispatch: got (%q, %v)", got, err)
	}
	// Document — routed to Tika.
	got, err = ex.Extract(context.Background(), []byte("%PDF"), "application/pdf")
	if err != nil || got != "tika-text" {
		t.Errorf("tika dispatch: got (%q, %v)", got, err)
	}
	// Ineligible — empty, no error.
	got, err = ex.Extract(context.Background(), []byte("data"), "image/png")
	if err != nil || got != "" {
		t.Errorf("ineligible dispatch: got (%q, %v)", got, err)
	}
}
