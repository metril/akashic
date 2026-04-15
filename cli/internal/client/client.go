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
	resp, err := c.get(ctx, "/api/search", v)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, string(body))
	}

	var results SearchResults
	if err := json.NewDecoder(resp.Body).Decode(&results); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &results, nil
}

func (c *Client) ListSources(ctx context.Context) ([]Source, error) {
	resp, err := c.get(ctx, "/api/sources", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, string(body))
	}

	var sources []Source
	if err := json.NewDecoder(resp.Body).Decode(&sources); err != nil {
		return nil, err
	}
	return sources, nil
}

func (c *Client) CreateSource(ctx context.Context, name, sourceType string, config map[string]string) (*Source, error) {
	body := map[string]interface{}{
		"name":   name,
		"type":   sourceType,
		"connection_config": config,
	}
	resp, err := c.post(ctx, "/api/sources", body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var source Source
	if err := json.NewDecoder(resp.Body).Decode(&source); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &source, nil
}

func (c *Client) TriggerScan(ctx context.Context, sourceName string) error {
	body := map[string]string{"source_name": sourceName}
	resp, err := c.post(ctx, "/api/scans/trigger", body)
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
	resp, err := c.get(ctx, "/api/scans", params)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, string(body))
	}

	var scans []Scan
	if err := json.NewDecoder(resp.Body).Decode(&scans); err != nil {
		return nil, err
	}
	return scans, nil
}

func (c *Client) ListDuplicates(ctx context.Context, minSize int64) ([]DuplicateGroup, error) {
	params := url.Values{}
	if minSize > 0 {
		params.Set("min_size", fmt.Sprintf("%d", minSize))
	}
	resp, err := c.get(ctx, "/api/duplicates", params)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, string(body))
	}

	var groups []DuplicateGroup
	if err := json.NewDecoder(resp.Body).Decode(&groups); err != nil {
		return nil, err
	}
	return groups, nil
}

func (c *Client) ListTags(ctx context.Context) ([]Tag, error) {
	resp, err := c.get(ctx, "/api/tags", nil)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("API error %d: %s", resp.StatusCode, string(body))
	}

	var tags []Tag
	if err := json.NewDecoder(resp.Body).Decode(&tags); err != nil {
		return nil, err
	}
	return tags, nil
}

func (c *Client) CreateTag(ctx context.Context, name string) (*Tag, error) {
	body := map[string]string{"name": name}
	resp, err := c.post(ctx, "/api/tags", body)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	var tag Tag
	if err := json.NewDecoder(resp.Body).Decode(&tag); err != nil {
		return nil, fmt.Errorf("decode: %w", err)
	}
	return &tag, nil
}

func (c *Client) TagFile(ctx context.Context, fileID, tagID string) error {
	path := fmt.Sprintf("/api/files/%s/tags/%s", fileID, tagID)
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

func (c *Client) PurgeSource(ctx context.Context, sourceID string) error {
	path := fmt.Sprintf("/api/purge/source/%s", sourceID)
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
