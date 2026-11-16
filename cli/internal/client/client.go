// Package client is the CLI's API transport: a thin layer over the generated
// apiclient that adds bearer auth (a user JWT or an `akp_` API token) and
// turns non-2xx replies into *APIError carrying the status and the server's
// error envelope. Wire shapes come from apiclient.
package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	"github.com/google/uuid"

	"github.com/akashic-project/akashic/cli/internal/apiclient"
)

type Client struct {
	api *apiclient.ClientWithResponses
}

// New builds a client for baseURL authenticating with apiKey. It fails only
// when baseURL is not a valid URL.
func New(baseURL, apiKey string) (*Client, error) {
	api, err := apiclient.NewClientWithResponses(baseURL,
		apiclient.WithHTTPClient(&http.Client{Timeout: 30 * time.Second}),
		apiclient.WithRequestEditorFn(func(_ context.Context, req *http.Request) error {
			req.Header.Set("Authorization", "Bearer "+apiKey)
			return nil
		}),
	)
	if err != nil {
		return nil, fmt.Errorf("api client for %q: %w", baseURL, err)
	}
	return &Client{api: api}, nil
}

// ParseID parses a user-supplied UUID argument.
func ParseID(kind, s string) (uuid.UUID, error) {
	id, err := uuid.Parse(s)
	if err != nil {
		return uuid.Nil, fmt.Errorf("invalid %s %q: not a UUID", kind, s)
	}
	return id, nil
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

// check returns an *APIError for any non-2xx status.
func check(status int, body []byte) error {
	if status >= 200 && status < 300 {
		return nil
	}
	return &APIError{Status: status, Body: string(body)}
}

// missingBody reports a 2xx reply that did not carry the documented JSON body.
func missingBody(status int, body []byte) error {
	return fmt.Errorf("API returned %d without the expected JSON body: %s", status, string(body))
}

// Search runs a full-text search. SourceID and Extension filter when set.
func (c *Client) Search(ctx context.Context, query string, params apiclient.SearchParams) (*apiclient.SearchPage, error) {
	params.Q = &query
	resp, err := c.api.SearchWithResponse(ctx, &params)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

func (c *Client) ListSources(ctx context.Context) ([]apiclient.SourceListResponse, error) {
	resp, err := c.api.ListSourcesWithResponse(ctx)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return *resp.JSON200, nil
}

func (c *Client) CreateSource(ctx context.Context, name, sourceType string, config map[string]string) (*apiclient.SourceResponse, error) {
	cfg := make(map[string]interface{}, len(config))
	for k, v := range config {
		cfg[k] = v
	}
	resp, err := c.api.CreateSourceWithResponse(ctx, apiclient.SourceCreate{
		Name: name, Type: sourceType, ConnectionConfig: cfg,
	})
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON201 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON201, nil
}

func (c *Client) TriggerScan(ctx context.Context, sourceName string) error {
	resp, err := c.api.TriggerScanWithResponse(ctx, apiclient.ScanTriggerRequest{SourceName: &sourceName})
	if err != nil {
		return err
	}
	return check(resp.StatusCode(), resp.Body)
}

func (c *Client) ListScans(ctx context.Context, limit int) ([]apiclient.ScanResponse, error) {
	params := &apiclient.ListScansParams{}
	if limit > 0 {
		params.Limit = &limit
	}
	resp, err := c.api.ListScansWithResponse(ctx, params)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200.Items, nil
}

// GetScan fetches a single scan by ID. Returns an APIError with
// Status=404 when the scan doesn't exist; callers can branch on that.
func (c *Client) GetScan(ctx context.Context, scanID uuid.UUID) (*apiclient.ScanResponse, error) {
	resp, err := c.api.GetScanWithResponse(ctx, scanID)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

// CancelScan marks a running scan as cancelled. Idempotent — cancelling
// an already-terminal scan returns success.
func (c *Client) CancelScan(ctx context.Context, scanID uuid.UUID) error {
	resp, err := c.api.CancelScanWithResponse(ctx, scanID)
	if err != nil {
		return err
	}
	return check(resp.StatusCode(), resp.Body)
}

func (c *Client) ListDuplicates(ctx context.Context, minSize int) ([]apiclient.DuplicateGroup, error) {
	params := &apiclient.ListDuplicatesParams{}
	if minSize > 0 {
		params.MinSize = &minSize
	}
	resp, err := c.api.ListDuplicatesWithResponse(ctx, params)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200.Items, nil
}

func (c *Client) ListTags(ctx context.Context) ([]apiclient.TagResponse, error) {
	resp, err := c.api.ListTagsWithResponse(ctx)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return *resp.JSON200, nil
}

func (c *Client) CreateTag(ctx context.Context, name string) (*apiclient.TagResponse, error) {
	resp, err := c.api.CreateTagWithResponse(ctx, apiclient.TagCreate{Name: name})
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON201 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON201, nil
}

// TagFile applies the named tag to an entry (POST /entries/{id}/tags, 204; the
// server creates the catalogue tag on demand) and returns that tag, looked up
// by name, since the apply call itself returns no body.
func (c *Client) TagFile(ctx context.Context, entryID uuid.UUID, tagName string) (*apiclient.TagResponse, error) {
	resp, err := c.api.ApplyTagsToEntryWithResponse(ctx, entryID, apiclient.ApplyTagsToEntryJSONRequestBody{Tags: []string{tagName}})
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	tags, err := c.ListTags(ctx)
	if err != nil {
		return nil, err
	}
	for i := range tags {
		if tags[i].Name == tagName {
			return &tags[i], nil
		}
	}
	return nil, fmt.Errorf("tag %q was applied but is missing from the tag catalogue", tagName)
}

func (c *Client) PurgeSource(ctx context.Context, sourceID uuid.UUID) error {
	resp, err := c.api.PurgeSourceWithResponse(ctx, sourceID)
	if err != nil {
		return err
	}
	return check(resp.StatusCode(), resp.Body)
}

// ListUsers returns every user account.
func (c *Client) ListUsers(ctx context.Context) ([]apiclient.UserResponse, error) {
	resp, err := c.api.ListUsersWithResponse(ctx)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return *resp.JSON200, nil
}

// SetUserRole sets a user's role. Setting a role pins it unless unpin is true,
// which hands the role back to IdP group mapping.
func (c *Client) SetUserRole(ctx context.Context, userID uuid.UUID, role string, unpin bool) (*apiclient.UserResponse, error) {
	r := apiclient.UserRoleUpdateRole(role)
	body := apiclient.UpdateUserRoleJSONRequestBody{Role: &r}
	if unpin {
		f := false
		body.RolePinned = &f
	}
	resp, err := c.api.UpdateUserRoleWithResponse(ctx, userID, body)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

// GetSource returns one source with its (secret-masked) connection config.
func (c *Client) GetSource(ctx context.Context, id uuid.UUID) (*apiclient.SourceResponse, error) {
	resp, err := c.api.GetSourceWithResponse(ctx, id)
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

// UpdateSourceConfig merges cfg into the source's connection_config.
func (c *Client) UpdateSourceConfig(ctx context.Context, id uuid.UUID, cfg map[string]any) (*apiclient.SourceResponse, error) {
	resp, err := c.api.UpdateSourceWithResponse(ctx, id, apiclient.UpdateSourceJSONRequestBody{ConnectionConfig: &cfg})
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return resp.JSON200, nil
}

// StorageByLinkRole returns the per-link-role file count and bytes of a source.
func (c *Client) StorageByLinkRole(ctx context.Context, id uuid.UUID) ([]apiclient.StorageByLinkRole, error) {
	resp, err := c.api.GetStorageByLinkRoleWithResponse(ctx, &apiclient.GetStorageByLinkRoleParams{SourceId: id})
	if err != nil {
		return nil, err
	}
	if err := check(resp.StatusCode(), resp.Body); err != nil {
		return nil, err
	}
	if resp.JSON200 == nil {
		return nil, missingBody(resp.StatusCode(), resp.Body)
	}
	return *resp.JSON200, nil
}

// ResolveSource finds a source by id, else by exact name.
func (c *Client) ResolveSource(ctx context.Context, nameOrID string) (*apiclient.SourceListResponse, error) {
	sources, err := c.ListSources(ctx)
	if err != nil {
		return nil, err
	}
	for i := range sources {
		if sources[i].Id.String() == nameOrID {
			return &sources[i], nil
		}
	}
	for i := range sources {
		if sources[i].Name == nameOrID {
			return &sources[i], nil
		}
	}
	return nil, fmt.Errorf("source %q not found", nameOrID)
}
