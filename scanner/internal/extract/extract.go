// Package extract turns file content into searchable plain text.
//
// v0.30.0 — extraction moved out of the Python `extraction-worker`
// and into the scanner. The worker could only read `local`/`nfs`
// files off a mounted disk; the scanner has connectors for every
// source type, so extraction now works for smb/s3/gdrive/onedrive
// too. Plain-text files are decoded natively here; document formats
// (PDF, Office, …) are sent to a co-located Apache Tika server.
package extract

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode/utf8"
)

// MaxExtractionSize caps the file size we will extract. Mirrors the
// Python worker's MAX_EXTRACTION_SIZE (50 MB) — a Tika round-trip on
// anything larger costs more than the search value it yields.
const MaxExtractionSize int64 = 50 * 1024 * 1024

// PlainTextTypes are MIME types decoded natively (UTF-8 / latin-1) —
// no Tika round-trip needed. Exact copy of the Python
// PLAIN_TEXT_TYPES set.
var PlainTextTypes = map[string]struct{}{
	"text/plain":                {},
	"text/html":                 {},
	"text/css":                  {},
	"text/javascript":           {},
	"text/xml":                  {},
	"application/json":          {},
	"application/xml":           {},
	"application/javascript":    {},
	"application/x-yaml":        {},
	"application/toml":          {},
}

// TikaTypes are document MIME types that require Apache Tika to
// extract text. Exact copy of the Python TIKA_TYPES set.
var TikaTypes = map[string]struct{}{
	"application/pdf":     {},
	"application/msword":  {},
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document":   {},
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":         {},
	"application/vnd.openxmlformats-officedocument.presentationml.presentation": {},
	"application/vnd.ms-excel":      {},
	"application/vnd.ms-powerpoint": {},
	"application/rtf":               {},
	"application/epub+zip":          {},
}

// IsEligible reports whether a file of the given MIME type and size
// should have its content extracted. A zero/negative size is treated
// as eligible (the connector may not report size); the per-read cap
// in the pool is the real defense against oversized files.
func IsEligible(mimeType string, sizeBytes int64) bool {
	if sizeBytes > MaxExtractionSize {
		return false
	}
	if mimeType == "" {
		return false
	}
	if _, ok := PlainTextTypes[mimeType]; ok {
		return true
	}
	if strings.HasPrefix(mimeType, "text/") {
		return true
	}
	_, ok := TikaTypes[mimeType]
	return ok
}

// needsTika reports whether a MIME type must go through Tika (vs.
// native plain-text decode).
func needsTika(mimeType string) bool {
	if _, ok := PlainTextTypes[mimeType]; ok {
		return false
	}
	if strings.HasPrefix(mimeType, "text/") {
		return false
	}
	_, ok := TikaTypes[mimeType]
	return ok
}

// ExtractPlain decodes text content natively: UTF-8 when valid,
// latin-1 (ISO-8859-1) as a fallback. Mirrors the Python
// extract_text_plain. Returns "" for empty / whitespace-only content.
func ExtractPlain(content []byte) string {
	var text string
	if utf8.Valid(content) {
		text = string(content)
	} else {
		// latin-1 → Unicode is the identity map for bytes 0–255, so a
		// per-byte rune conversion is a correct ISO-8859-1 decode and
		// needs no external dependency.
		runes := make([]rune, len(content))
		for i, b := range content {
			runes[i] = rune(b)
		}
		text = string(runes)
	}
	return strings.TrimSpace(text)
}

// Extractor extracts text from document content via Apache Tika.
// A zero tikaURL disables Tika extraction (plain-text still works).
type Extractor struct {
	tikaURL    string
	httpClient *http.Client
}

// NewExtractor builds an Extractor pointed at the given Tika base URL
// (e.g. http://tika:9998). An empty tikaURL means document extraction
// is skipped — ExtractTika returns ("", nil) — while plain-text
// extraction continues to work.
func NewExtractor(tikaURL string) *Extractor {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConnsPerHost = 8
	t.IdleConnTimeout = 90 * time.Second
	return &Extractor{
		tikaURL: strings.TrimRight(tikaURL, "/"),
		httpClient: &http.Client{
			Timeout:   90 * time.Second,
			Transport: t,
		},
	}
}

// TikaEnabled reports whether document extraction is configured.
func (e *Extractor) TikaEnabled() bool { return e.tikaURL != "" }

// ExtractTika PUTs document bytes to Tika's /tika endpoint and returns
// the extracted plain text. Mirrors the Python extract_text_tika:
// Content-Type carries the source MIME, Accept asks for text/plain.
func (e *Extractor) ExtractTika(ctx context.Context, content []byte, mimeType string) (string, error) {
	if e.tikaURL == "" {
		return "", nil
	}
	reqCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(
		reqCtx, http.MethodPut, e.tikaURL+"/tika", bytes.NewReader(content),
	)
	if err != nil {
		return "", err
	}
	if mimeType != "" {
		req.Header.Set("Content-Type", mimeType)
	}
	req.Header.Set("Accept", "text/plain")
	resp, err := e.httpClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		return "", fmt.Errorf("tika HTTP %d: %s", resp.StatusCode, string(raw))
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(raw)), nil
}

// Extract dispatches content to native plain-text decoding or Tika
// based on the MIME type. Returns "" for ineligible types.
func (e *Extractor) Extract(ctx context.Context, content []byte, mimeType string) (string, error) {
	if needsTika(mimeType) {
		return e.ExtractTika(ctx, content, mimeType)
	}
	if _, ok := PlainTextTypes[mimeType]; ok || strings.HasPrefix(mimeType, "text/") {
		return ExtractPlain(content), nil
	}
	return "", nil
}
