package models

import "time"

type FileEntry struct {
	Path        string     `json:"path"`
	Filename    string     `json:"filename"`
	Extension   string     `json:"extension,omitempty"`
	SizeBytes   int64      `json:"size_bytes"`
	MimeType    string     `json:"mime_type,omitempty"`
	ContentHash string     `json:"content_hash,omitempty"`
	Permissions string     `json:"permissions,omitempty"`
	Owner       string     `json:"owner,omitempty"`
	Group       string     `json:"file_group,omitempty"`
	CreatedAt   *time.Time `json:"fs_created_at,omitempty"`
	ModifiedAt  *time.Time `json:"fs_modified_at,omitempty"`
	AccessedAt  *time.Time `json:"fs_accessed_at,omitempty"`
	IsDir       bool       `json:"is_dir"`
}

type ScanBatch struct {
	SourceID string      `json:"source_id"`
	ScanID   string      `json:"scan_id"`
	Files    []FileEntry `json:"files"`
	IsFinal  bool        `json:"is_final"`
}

type ScanRequest struct {
	SourceID        string   `json:"source_id"`
	ScanID          string   `json:"scan_id"`
	ScanType        string   `json:"scan_type"`
	ExcludePatterns []string `json:"exclude_patterns,omitempty"`
}
