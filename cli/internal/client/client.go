package client

import (
	"context"
	"encoding/json"
	"fmt"
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

func New(baseURL, apiKey string) *Client {
	return &Client{
		baseURL:    baseURL,
		apiKey:     apiKey,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
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

	var sources []Source
	if err := json.NewDecoder(resp.Body).Decode(&sources); err != nil {
		return nil, err
	}
	return sources, nil
}
