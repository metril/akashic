package models

import (
	"encoding/json"
	"fmt"
	"time"
)

// ---- Discriminated-union ACL types ----

// ACL is the wire shape sent to the API. The Type discriminator selects which
// of the typed sub-fields are populated; consumers call MarshalJSON to emit
// the per-type discriminated shape.
type ACL struct {
	Type           string     `json:"type"` // "posix" | "nfsv4" | "nt" | "s3" | "cloud_drive"
	Entries        []PosixACE `json:"entries,omitempty"`
	DefaultEntries []PosixACE `json:"default_entries,omitempty"`

	// NFSv4-specific
	NfsV4Entries []NfsV4ACE `json:"-"`

	// NT-specific
	Owner     *NtPrincipal `json:"owner,omitempty"`
	Group     *NtPrincipal `json:"group,omitempty"`
	Control   []string     `json:"control,omitempty"`
	NtEntries []NtACE      `json:"-"`

	// S3-specific
	S3Owner  *S3Owner  `json:"-"`
	S3Grants []S3Grant `json:"-"`

	// Cloud-drive-specific (Drive/OneDrive/Dropbox).
	CloudDriveGrants     []CloudDriveGrant `json:"-"`
	CloudDriveDomain     string            `json:"-"`
}

// PosixACE is one POSIX ACL entry.
type PosixACE struct {
	Tag       string `json:"tag"`                 // user, group, mask, other, user_obj, group_obj
	Qualifier string `json:"qualifier,omitempty"`
	Perms     string `json:"perms"`               // "rwx" style
}

// NfsV4ACE — kept here so all ACL shapes live in one place.
type NfsV4ACE struct {
	Principal string   `json:"principal"`
	AceType   string   `json:"ace_type"` // allow | deny | audit | alarm
	Flags     []string `json:"flags,omitempty"`
	Mask      []string `json:"mask,omitempty"`
}

// NtPrincipal — owner/group/ACE subject in an NT ACL.
type NtPrincipal struct {
	Sid  string `json:"sid"`
	Name string `json:"name,omitempty"`
}

type NtACE struct {
	Sid     string   `json:"sid"`
	Name    string   `json:"name,omitempty"`
	AceType string   `json:"ace_type"` // allow | deny | audit
	Flags   []string `json:"flags,omitempty"`
	Mask    []string `json:"mask,omitempty"`
}

type S3Owner struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name,omitempty"`
}

type S3Grant struct {
	GranteeType string `json:"grantee_type"`
	GranteeID   string `json:"grantee_id,omitempty"`
	GranteeName string `json:"grantee_name,omitempty"`
	Permission  string `json:"permission"`
}

// CloudDrivePrincipal is one principal in a cloud-drive grant — see
// api/akashic/schemas/acl.py for the wire-shape contract.
type CloudDrivePrincipal struct {
	Type  string `json:"type"` // user | group | anyone | domain
	ID    string `json:"id"`
	Email string `json:"email,omitempty"`
	Name  string `json:"name,omitempty"`
}

// CloudDriveLink is the shareable link an "anyone-with-link" grant rides on.
type CloudDriveLink struct {
	ID    string `json:"id"`
	Scope string `json:"scope"` // anyone | domain | restricted
}

// CloudDriveGrant is one (principal, role) sharing grant on a cloud-drive entry.
type CloudDriveGrant struct {
	Principal         CloudDrivePrincipal `json:"principal"`
	Role              string              `json:"role"` // owner | writer | commenter | reader | file_organizer
	Link              *CloudDriveLink     `json:"link,omitempty"`
	Inherited         bool                `json:"inherited,omitempty"`
	InheritedFromID   string              `json:"inherited_from_id,omitempty"`
	InheritedFromPath string              `json:"inherited_from_path,omitempty"`
}

// MarshalJSON emits the discriminated-union shape per Type.
func (a *ACL) MarshalJSON() ([]byte, error) {
	if a == nil {
		return []byte("null"), nil
	}
	switch a.Type {
	case "posix":
		out := map[string]interface{}{
			"type":    "posix",
			"entries": a.Entries,
		}
		if a.DefaultEntries != nil {
			out["default_entries"] = a.DefaultEntries
		}
		return json.Marshal(out)
	case "nfsv4":
		return json.Marshal(map[string]interface{}{
			"type":    "nfsv4",
			"entries": a.NfsV4Entries,
		})
	case "nt":
		out := map[string]interface{}{
			"type":    "nt",
			"entries": a.NtEntries,
		}
		if a.Owner != nil {
			out["owner"] = a.Owner
		}
		if a.Group != nil {
			out["group"] = a.Group
		}
		if a.Control != nil {
			out["control"] = a.Control
		}
		return json.Marshal(out)
	case "s3":
		out := map[string]interface{}{
			"type":   "s3",
			"grants": a.S3Grants,
		}
		if a.S3Owner != nil {
			out["owner"] = a.S3Owner
		}
		return json.Marshal(out)
	case "cloud_drive":
		out := map[string]interface{}{
			"type":   "cloud_drive",
			"grants": a.CloudDriveGrants,
		}
		if a.CloudDriveDomain != "" {
			out["domain_restricted_to"] = a.CloudDriveDomain
		}
		return json.Marshal(out)
	}
	return nil, fmt.Errorf("acl: unknown type %q", a.Type)
}

// EntryRecord is one observation of a filesystem entry (file or directory).
type EntryRecord struct {
	Path        string `json:"path"`
	Name        string `json:"name"`
	Kind        string `json:"kind"` // "file" | "directory"
	// v0.13.0 — provider-specific opaque identifier for cloud-drive
	// connectors (Drive/OneDrive/Dropbox). Empty on filesystem-shape
	// connectors. Used by the API for permission / metadata lookups
	// that have to round-trip to the provider.
	NativeID    string `json:"native_id,omitempty"`
	Extension   string `json:"extension,omitempty"`
	SizeBytes   *int64 `json:"size_bytes,omitempty"`
	MimeType    string `json:"mime_type,omitempty"`
	ContentHash string `json:"content_hash,omitempty"`

	Mode      *uint32           `json:"mode,omitempty"`
	Uid       *uint32           `json:"uid,omitempty"`
	Gid       *uint32           `json:"gid,omitempty"`
	OwnerName string            `json:"owner_name,omitempty"`
	GroupName string            `json:"group_name,omitempty"`
	Acl       *ACL              `json:"acl,omitempty"`
	Xattrs    map[string]string `json:"xattrs,omitempty"`

	CreatedAt  *time.Time `json:"fs_created_at,omitempty"`
	ModifiedAt *time.Time `json:"fs_modified_at,omitempty"`
	AccessedAt *time.Time `json:"fs_accessed_at,omitempty"`

	// Phase B — directory-only post-order rollups. Set by connectors
	// that walk depth-first; nil from connectors that can't compute
	// them cheaply (the API-side rollup CTE backfills those as a
	// NULL-only safety net).
	SubtreeSizeBytes  *int64 `json:"subtree_size_bytes,omitempty"`
	SubtreeFileCount  *int64 `json:"subtree_file_count,omitempty"`
	SubtreeDirCount   *int64 `json:"subtree_dir_count,omitempty"`

	// v0.6.0 — provider-specific metadata for self-hosted libraries
	// (Paperless-ngx: correspondent, document_type, custom_fields;
	// Immich: camera EXIF, person/face, GPS, datetime_original, album).
	// Filesystem connectors leave this nil. Schemaless on purpose; the
	// api stores the dict as-is in entries.domain_metadata, and a known
	// subset of keys is exposed as Meilisearch filterable attributes.
	DomainMetadata map[string]interface{} `json:"domain_metadata,omitempty"`
}

func (e *EntryRecord) IsDir() bool { return e.Kind == "directory" }

type ScanBatch struct {
	SourceID               string                  `json:"source_id"`
	ScanID                 string                  `json:"scan_id"`
	Entries                []EntryRecord           `json:"entries"`
	IsFinal                bool                    `json:"is_final"`
	SourceSecurityMetadata *SourceSecurityMetadata `json:"source_security_metadata,omitempty"`
	// v0.5.11 — only set on the final batch (IsFinal=true). Counts of
	// directory/file entries the connector silently skipped during the
	// walk (permission denied, ENOENT mid-scan). The api persists these
	// on the Scan row so SourceDetail can surface them. Omitted on
	// intermediate batches; defaults to 0 on legacy scanners that
	// don't send the field.
	InaccessibleDirs  int `json:"inaccessible_dirs,omitempty"`
	InaccessibleFiles int `json:"inaccessible_files,omitempty"`
}

// ExtractCandidate is a new-or-changed file the API flagged for
// content extraction in a batch response. v0.30.0 — the scanner
// extracts only files the API marked new/changed, not every file
// every scan.
type ExtractCandidate struct {
	Path      string `json:"path"`
	MimeType  string `json:"mime_type"`
	SizeBytes int64  `json:"size_bytes"`
}

// BatchResponse is the JSON body /api/ingest/batch returns. v0.30.0 —
// previously the scanner ignored the response body; it now reads
// extract_candidates to drive scanner-side text extraction.
type BatchResponse struct {
	ExtractCandidates []ExtractCandidate `json:"extract_candidates"`
}

// ContentItem is one file's extracted text, posted to
// /api/ingest/content. v0.30.0.
type ContentItem struct {
	Path        string `json:"path"`
	ContentText string `json:"content_text"`
}

// ContentBatch carries a set of extracted-text records back to the
// API for Meilisearch indexing. v0.30.0.
type ContentBatch struct {
	SourceID string        `json:"source_id"`
	ScanID   string        `json:"scan_id"`
	Items    []ContentItem `json:"items"`
}

// SourceSecurityMetadata is sent at scan-start for S3 sources.
type SourceSecurityMetadata struct {
	CapturedAt          string                 `json:"captured_at"`
	BucketAcl           map[string]interface{} `json:"bucket_acl,omitempty"`
	BucketPolicyPresent bool                   `json:"bucket_policy_present"`
	BucketPolicy        map[string]interface{} `json:"bucket_policy,omitempty"`
	PublicAccessBlock   *PublicAccessBlock     `json:"public_access_block,omitempty"`
	IsPublicInferred    bool                   `json:"is_public_inferred"`
}

type PublicAccessBlock struct {
	BlockPublicAcls       bool `json:"block_public_acls"`
	IgnorePublicAcls      bool `json:"ignore_public_acls"`
	BlockPublicPolicy     bool `json:"block_public_policy"`
	RestrictPublicBuckets bool `json:"restrict_public_buckets"`
}

type ScanRequest struct {
	SourceID        string   `json:"source_id"`
	ScanID          string   `json:"scan_id"`
	ScanType        string   `json:"scan_type"`
	ExcludePatterns []string `json:"exclude_patterns,omitempty"`
}
