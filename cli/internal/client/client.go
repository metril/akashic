package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

type SearchResults struct {
	Results []FileEntry `json:"results"`
	Total   int         `json:"total"`
	Query   string      `json:"query"`
}

type FileEntry struct {
	ID        string `json:"id"`
	SourceID  string `json:"source_id"`
	Path      string `json:"path"`
	Filename  string `json:"filename"`
	Extension string `json:"extension"`
	SizeBytes int64  `json:"size_bytes"`
	MimeType  string `json:"mime_type"`
	IsDeleted bool   `json:"is_deleted"`
}

type Source struct {
	ID         string `json:"id"`
	Name       string `json:"name"`
	Type       string `json:"type"`
	Status     string `json:"status"`
	LastScanAt string `json:"last_scan_at"`
}

type SearchParams struct {
	SourceID  string
	Extension string
	MinSize   int64
	MaxSize   int64
}

type Scan struct {
	ID         string `json:"id"`
	SourceID   string `json:"source_id"`
	Status     string `json:"status"`
	FilesFound int    `json:"files_found"`
	StartedAt  string `json:"started_at"`
}

type DuplicateGroup struct {
	ContentHash string `json:"content_hash"`
	Count       int    `json:"count"`
	TotalSize   int64  `json:"total_size"`
	FileSize    int64  `json:"file_size"`
	WastedBytes int64  `json:"wasted_bytes"`
}

type Tag struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Color string `json:"color"`
}

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL:    baseURL,
		apiKey:     apiKey,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *Client) post(ctx context.Context, path string, body interface{}) (*http.Response, error) {
	var buf bytes.Buffer
	if body != nil {
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			return nil, err
		}
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, &buf)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	req.Header.Set("Content-Type", "application/json")
	return c.httpClient.Do(req)
}

func (c *Client) get(ctx context.Context, path string, params url.Values) (*http.Response, error) {
	u := c.baseURL + path
	if len(params) > 0 {
		u += "?" + params.Encode()
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	return c.httpClient.Do(req)
}

func (c *Client) Search(ctx context.Context, query string, params *SearchParams) (*SearchResults, error) {
	v := url.Values{"q": {query}}
	if params != nil {
		if params.SourceID != "" {
			v.Set("source_id", params.SourceID)
		}
		if params.Extension != "" {
			v.Set("extension", params.Extension)
		}
	}
	resp, err := c.get(ctx, "/api/v1/search", v)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, apiMessage(string(body)))
	}

	var results SearchResults
	if err := json.NewDecoder(resp.Body).Decode(&results); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &results, nil
}

func (c *Client) ListSources(ctx context.Context) ([]Source, error) {
	resp, err := c.get(ctx, "/api/v1/sources", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, apiMessage(string(body)))
	}

	var sources []Source
	if err := json.NewDecoder(resp.Body).Decode(&sources); err != nil {
		return nil, err
	}
	return sources, nil
}

// checkStatus returns an error carrying the response body when the
// server answered with a 4xx/5xx status.
func checkStatus(resp *http.Response) error {
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return fmt.Errorf("API error %d: %s", resp.StatusCode, apiMessage(string(body)))
	}
	return nil
}

func (c *Client) CreateSource(ctx context.Context, name, sourceType string, config map[string]string) (*Source, error) {
	body := map[string]interface{}{
		"name":   name,
		"type":   sourceType,
		"connection_config": config,
	}
	resp, err := c.post(ctx, "/api/v1/sources", body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if err := checkStatus(resp); err != nil {
		return nil, err
	}

	var source Source
	if err := json.NewDecoder(resp.Body).Decode(&source); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &source, nil
}

func (c *Client) TriggerScan(ctx context.Context, sourceName string) error {
	body := map[string]string{"source_name": sourceName}
	resp, err := c.post(ctx, "/api/v1/scans/trigger", body)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("server returned %d", resp.StatusCode)
	}
	return nil
}

func (c *Client) ListScans(ctx context.Context, limit int) ([]Scan, error) {
	params := url.Values{}
	if limit > 0 {
		params.Set("limit", fmt.Sprintf("%d", limit))
	}
	resp, err := c.get(ctx, "/api/v1/scans", params)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, &APIError{Status: resp.StatusCode, Body: string(body)}
	}

	var page struct {
		Items []Scan `json:"items"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		return nil, err
	}
	return page.Items, nil
}

// GetScan fetches a single scan by ID. Returns an APIError with
// Status=404 when the scan doesn't exist; callers can branch on that.
func (c *Client) GetScan(ctx context.Context, scanID string) (*Scan, error) {
	resp, err := c.get(ctx, "/api/v1/scans/"+scanID, nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, &APIError{Status: resp.StatusCode, Body: string(body)}
	}
	var s Scan
	if err := json.NewDecoder(resp.Body).Decode(&s); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &s, nil
}

// CancelScan marks a running scan as cancelled. Idempotent — cancelling
// an already-terminal scan returns success.
func (c *Client) CancelScan(ctx context.Context, scanID string) error {
	resp, err := c.post(ctx, "/api/v1/scans/"+scanID+"/cancel", nil)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return &APIError{Status: resp.StatusCode, Body: string(body)}
	}
	return nil
}

// apiMessage returns the `message` of the API's error envelope
// ({code, message, detail?, request_id}), or the raw body when it isn't one.
func apiMessage(body string) string {
	var env struct {
		Message string `json:"message"`
	}
	if json.Unmarshal([]byte(body), &env) == nil && env.Message != "" {
		return env.Message
	}
	return body
}

// APIError carries the HTTP status alongside the server's response body
// so the CLI can translate it into a meaningful exit code (1 for 4xx,
// 2 for 5xx, etc).
type APIError struct {
	Status int
	Body   string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("API error %d: %s", e.Status, apiMessage(e.Body))
}

func (c *Client) ListDuplicates(ctx context.Context, minSize int64) ([]DuplicateGroup, error) {
	params := url.Values{}
	if minSize > 0 {
		params.Set("min_size", fmt.Sprintf("%d", minSize))
	}
	resp, err := c.get(ctx, "/api/v1/duplicates", params)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, apiMessage(string(body)))
	}

	var page struct {
		Items []DuplicateGroup `json:"items"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		return nil, err
	}
	return page.Items, nil
}

func (c *Client) ListTags(ctx context.Context) ([]Tag, error) {
	resp, err := c.get(ctx, "/api/v1/tags", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, apiMessage(string(body)))
	}

	var tags []Tag
	if err := json.NewDecoder(resp.Body).Decode(&tags); err != nil {
		return nil, err
	}
	return tags, nil
}

func (c *Client) CreateTag(ctx context.Context, name string) (*Tag, error) {
	body := map[string]string{"name": name}
	resp, err := c.post(ctx, "/api/v1/tags", body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if err := checkStatus(resp); err != nil {
		return nil, err
	}

	var tag Tag
	if err := json.NewDecoder(resp.Body).Decode(&tag); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &tag, nil
}

// TagFile applies the named tag to an entry (POST /entries/{id}/tags, 204).
func (c *Client) TagFile(ctx context.Context, entryID, tagName string) error {
	path := fmt.Sprintf("/api/v1/entries/%s/tags", entryID)
	resp, err := c.post(ctx, path, map[string][]string{"tags": {tagName}})
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("server returned %d", resp.StatusCode)
	}
	return nil
}

func (c *Client) PurgeSource(ctx context.Context, sourceID string) error {
	path := fmt.Sprintf("/api/v1/purge/source/%s", sourceID)
	resp, err := c.post(ctx, path, nil)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("server returned %d", resp.StatusCode)
	}
	return nil
}
