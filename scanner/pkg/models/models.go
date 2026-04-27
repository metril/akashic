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
	Type           string     `json:"type"` // "posix" | "nfsv4" | "nt" | "s3"
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
	}
	return nil, fmt.Errorf("acl: unknown type %q", a.Type)
}

// EntryRecord is one observation of a filesystem entry (file or directory).
type EntryRecord struct {
	Path        string `json:"path"`
	Name        string `json:"name"`
	Kind        string `json:"kind"` // "file" | "directory"
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
}

func (e *EntryRecord) IsDir() bool { return e.Kind == "directory" }

type ScanBatch struct {
	SourceID                string                  `json:"source_id"`
	ScanID                  string                  `json:"scan_id"`
	Entries                 []EntryRecord           `json:"entries"`
	IsFinal                 bool                    `json:"is_final"`
	SourceSecurityMetadata  *SourceSecurityMetadata `json:"source_security_metadata,omitempty"`
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
