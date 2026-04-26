package models

import "time"

// ACLEntry represents a single POSIX ACL entry.
type ACLEntry struct {
	Tag       string `json:"tag"`                 // user, group, mask, other, user_obj, group_obj
	Qualifier string `json:"qualifier,omitempty"` // username/groupname for user/group tags
	Perms     string `json:"perms"`               // "rwx", "r-x", etc.
}

// EntryRecord is one observation of a filesystem entry (file or directory).
type EntryRecord struct {
	Path        string            `json:"path"`
	Name        string            `json:"name"`
	Kind        string            `json:"kind"` // "file" | "directory"
	Extension   string            `json:"extension,omitempty"`
	SizeBytes   *int64            `json:"size_bytes,omitempty"`
	MimeType    string            `json:"mime_type,omitempty"`
	ContentHash string            `json:"content_hash,omitempty"`

	// Permissions
	Mode       *uint32           `json:"mode,omitempty"`
	Uid        *uint32           `json:"uid,omitempty"`
	Gid        *uint32           `json:"gid,omitempty"`
	OwnerName  string            `json:"owner_name,omitempty"`
	GroupName  string            `json:"group_name,omitempty"`
	Acl        []ACLEntry        `json:"acl,omitempty"`
	Xattrs     map[string]string `json:"xattrs,omitempty"`

	// Filesystem timestamps
	CreatedAt  *time.Time `json:"fs_created_at,omitempty"`
	ModifiedAt *time.Time `json:"fs_modified_at,omitempty"`
	AccessedAt *time.Time `json:"fs_accessed_at,omitempty"`
}

// IsDir reports whether this record represents a directory.
func (e *EntryRecord) IsDir() bool {
	return e.Kind == "directory"
}

type ScanBatch struct {
	SourceID string        `json:"source_id"`
	ScanID   string        `json:"scan_id"`
	Entries  []EntryRecord `json:"entries"`
	IsFinal  bool          `json:"is_final"`
}

type ScanRequest struct {
	SourceID        string   `json:"source_id"`
	ScanID          string   `json:"scan_id"`
	ScanType        string   `json:"scan_type"`
	ExcludePatterns []string `json:"exclude_patterns,omitempty"`
}
