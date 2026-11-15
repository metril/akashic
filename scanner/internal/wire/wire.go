// Package wire holds the small helpers the scanner needs on top of the
// generated API types in internal/apiclient: pointer constructors for the
// optional fields, ACL union constructors, and entry predicates. It holds no
// API shapes of its own.
package wire

import (
	"fmt"

	"github.com/akashic-project/akashic/scanner/internal/apiclient"
)

// To returns a pointer to v.
func To[T any](v T) *T { return &v }

// Str returns a pointer to s, or nil when s is empty (the optional string
// fields of the wire types omit empty values).
func Str(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// Strs returns a pointer to s, or nil when s is empty.
func Strs(s []string) *[]string {
	if len(s) == 0 {
		return nil
	}
	return &s
}

// Deref returns *p, or the zero value when p is nil.
func Deref[T any](p *T) T {
	var zero T
	if p == nil {
		return zero
	}
	return *p
}

// IsDir reports whether the entry is a directory.
func IsDir(e *apiclient.EntryIn) bool {
	return e.Kind != nil && *e.Kind == apiclient.Directory
}

// KindOf returns the entry kind for a directory flag.
func KindOf(isDir bool) *apiclient.EntryInKind {
	if isDir {
		return To(apiclient.Directory)
	}
	return To(apiclient.File)
}

// The ACL constructors marshal plain structs of strings and slices, which
// cannot fail; a failure is a programming error, so they panic rather than
// hand back a half-built union.
func must(err error) {
	if err != nil {
		panic(fmt.Sprintf("wire: build ACL: %v", err))
	}
}

// PosixACL builds the posix ACL union. defaults is omitted when nil.
func PosixACL(entries, defaults []apiclient.PosixACEInput) *apiclient.ACLInput {
	if entries == nil {
		entries = []apiclient.PosixACEInput{}
	}
	v := apiclient.PosixACLInput{Entries: entries}
	if defaults != nil {
		v.DefaultEntries = &defaults
	}
	var u apiclient.ACLInput
	must(u.FromPosixACLInput(v))
	return &u
}

// NfsV4ACL builds the nfsv4 ACL union.
func NfsV4ACL(entries []apiclient.NfsV4ACEInput) *apiclient.ACLInput {
	if entries == nil {
		entries = []apiclient.NfsV4ACEInput{}
	}
	var u apiclient.ACLInput
	must(u.FromNfsV4ACLInput(apiclient.NfsV4ACLInput{Entries: entries}))
	return &u
}

// NtACL builds the nt ACL union.
func NtACL(v apiclient.NtACLInput) *apiclient.ACLInput {
	if v.Entries == nil {
		v.Entries = []apiclient.NtACEInput{}
	}
	var u apiclient.ACLInput
	must(u.FromNtACLInput(v))
	return &u
}

// S3ACL builds the s3 ACL union.
func S3ACL(v apiclient.S3ACLInput) *apiclient.ACLInput {
	if v.Grants == nil {
		v.Grants = &[]apiclient.S3GrantInput{}
	}
	var u apiclient.ACLInput
	must(u.FromS3ACLInput(v))
	return &u
}

// CloudDriveACL builds the cloud_drive ACL union. domain is omitted when empty.
func CloudDriveACL(grants []apiclient.CloudDriveGrantInput, domain string) *apiclient.ACLInput {
	if grants == nil {
		grants = []apiclient.CloudDriveGrantInput{}
	}
	var u apiclient.ACLInput
	must(u.FromCloudDriveACLInput(apiclient.CloudDriveACLInput{Grants: &grants, DomainRestrictedTo: Str(domain)}))
	return &u
}
