export interface User {
  id: string;
  username: string;
  email: string;
  created_at: string;
}

export interface PublicAccessBlock {
  block_public_acls: boolean;
  ignore_public_acls: boolean;
  block_public_policy: boolean;
  restrict_public_buckets: boolean;
}

export interface Host {
  id: string;
  name: string;
  type: string;
  // Host-only connection config — host/port/credentials. Returned with
  // secrets masked as "***" (same convention as Source.connection_config).
  connection_config: Record<string, unknown>;
  source_count: number;
  // v0.5.9 — optional reusable credential profile reference.
  credential_profile_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface HostInline {
  id: string;
  name: string;
  type: string;
}

export interface SourceSecurityMetadata {
  captured_at: string;
  bucket_acl: Record<string, unknown> | null;
  bucket_policy_present: boolean;
  bucket_policy: Record<string, unknown> | null;
  public_access_block: PublicAccessBlock | null;
  is_public_inferred: boolean;
}

export interface Source {
  id: string;
  name: string;
  type: string;
  // Optional FK to a Host that owns the connection-level config
  // (hostname, credentials). NULL only for `local` sources.
  host_id: string | null;
  // Inlined host shape (id + name + type only — credentials live
  // behind GET /api/hosts/{id}). Present whenever host_id is set.
  host: HostInline | null;
  // Optional since v0.4.3 — the lean list endpoint omits these
  // heavy fields. Per-source detail (GET /sources/{id}) returns
  // them. UI code that needs them should fetch via
  // useSourceDetail(id), not the list query.
  connection_config?: Record<string, unknown>;
  exclude_patterns?: string[] | null;
  security_metadata?: SourceSecurityMetadata | null;
  // Server-rendered subtitle for the SourceCard, only on the lean
  // list shape. Detail panel ignores it (computes its own).
  summary?: string;
  scan_schedule: string | null;
  preferred_pool: string | null;
  // Max distinct scanners that may hold work-unit leases on the same
  // scan simultaneously. 1 = legacy (one scanner walks the whole
  // tree). Higher values let scanners cooperate via the work-units
  // queue (Phase 2 of v0.5.x — scanner-side support lands in a
  // follow-up release).
  max_parallel_scanners: number;
  last_scan_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  // External / removable storage hint (USB drive, intermittent
  // network mount). Used by the UI to label intermittent sources;
  // actual reachability lives in /sources/{id}/reachability-summary.
  is_removable: boolean;
  // v0.5.9 — optional reusable credential profile reference.
  credential_profile_id: string | null;
}

export interface FileVersion {
  id: string;
  file_id: string;
  size_bytes: number;
  content_hash: string;
  scanned_at: string;
}

export interface FileLocation {
  id: string;
  file_id: string;
  // Nullable since v0.4.0 — see FileEntry.source_id below.
  source_id: string | null;
  path: string;
  source?: Source;
}

export interface FileEntry {
  id: string;
  filename: string;
  extension: string | null;
  size_bytes: number | null;
  content_hash: string | null;
  mime_type: string | null;
  fs_modified_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  is_deleted: boolean;
  // Nullable since v0.4.0: when a source is deleted with the
  // default "preserve entries" flavour, surviving entries land
  // here with source_id=null. UI renders "(deleted source)".
  source_id: string | null;
  path: string;
  source?: Source;
  tags?: Tag[];
}

export interface SearchResult {
  id: string;
  filename: string;
  extension: string | null;
  size_bytes: number | null;
  content_hash: string | null;
  mime_type: string | null;
  path: string;
  source_id: string | null;
  fs_modified_at: number | null;
  tags: string[];
  // v0.20.0 — number of *other* entries that share content_hash within
  // the user's permitted source set. Populated server-side per request
  // (not indexed in Meili because it churns on every other row's hash
  // mutation). Zero or absent when the row is unique.
  dup_count?: number;
}

export interface SearchResults {
  results: SearchResult[];
  total: number;
  query: string;
  // v0.6.0 — Meilisearch facet distribution for the `domain_metadata.*`
  // keys present in the current result set (Library Metadata facet
  // panel). Keyed by the public dotted name (`domain_metadata.album`),
  // not the underscore-flattened wire shape. Null when the SQL
  // fallback path served the request.
  facet_distribution?: Record<string, Record<string, number>> | null;
}

export interface DuplicateGroup {
  content_hash: string;
  count: number;
  total_size: number;
  file_size: number;
  wasted_bytes: number;
}

export interface Scan {
  id: string;
  source_id: string;
  scan_type: string;
  status: string;
  files_found: number;
  files_new: number;
  files_changed: number;
  files_deleted: number;
  started_at: string | null;
  completed_at: string | null;
  error_message?: string | null;
  // Phase 1 — heartbeat-driven progress fields. All nullable for legacy
  // rows scanned with the old scanner.
  current_path?: string | null;
  last_heartbeat_at?: string | null;
  bytes_scanned_so_far?: number | null;
  files_skipped?: number;
  dirs_walked?: number;
  dirs_queued?: number;
  total_estimated?: number | null;
  phase?: "prewalk" | "walk" | "finalize" | null;
  previous_scan_files?: number | null;
  // v0.5.11 — entries the scanner silently skipped during walk
  // (permission denied, ENOENT mid-scan). 0 on legacy rows.
  inaccessible_dirs?: number;
  inaccessible_files?: number;
  source?: Source;
}

export interface ScanLogLine {
  id: string;
  ts: string;
  level: "info" | "warn" | "error" | "stderr";
  message: string;
  // v0.28.2 — attribution to the scanner that produced this row.
  // Populated from the scanner_id claim baked into the ingest JWT
  // at lease time. Null for legacy rows (pre-v0.28.2) and for rows
  // produced before the agent's JWT carried the claim.
  scanner_id?: string | null;
  scanner_name?: string | null;
}

export interface ScanSnapshot {
  kind: "snapshot";
  scan_id: string;
  source_id: string;
  status: string;
  phase: string | null;
  current_path: string | null;
  files_found: number;
  files_new: number;
  files_changed: number;
  files_deleted: number;
  files_skipped: number;
  bytes_scanned_so_far: number | null;
  dirs_walked: number;
  dirs_queued: number;
  total_estimated: number | null;
  previous_scan_files: number | null;
  started_at: string | null;
  completed_at: string | null;
  last_heartbeat_at: string | null;
  error_message: string | null;
  recent_lines: ScanLogLine[];
}

export interface ScanProgressEvent {
  kind: "progress";
  scan_id: string;
  current_path: string | null;
  files_scanned: number;
  bytes_scanned: number;
  files_skipped: number;
  dirs_walked: number;
  dirs_queued: number;
  total_estimated: number | null;
  phase: string | null;
  ts: string;
}

export interface ScanLogEvent {
  kind: "log" | "stderr";
  scan_id: string;
  lines: ScanLogLine[];
}

export type ScanWsEvent =
  | ScanSnapshot
  | ScanProgressEvent
  | ScanLogEvent
  | { kind: "ping" };

export interface Tag {
  id: string;
  name: string;
  color: string | null;
  created_at: string;
}

export interface StorageByType {
  extension: string;
  count: number;
  total_size: number;
}

export interface StorageBySource {
  source_id: string;
  source_name: string;
  count: number;
  total_size: number;
}

// ---- Browse / Entry types ----

// ---- ACL discriminated-union types ----

export type ACLType = "posix" | "nfsv4" | "nt" | "s3" | "cloud_drive";

export interface PosixACE {
  tag: string;
  qualifier: string;
  perms: string;
}

export interface PosixACL {
  type: "posix";
  entries: PosixACE[];
  default_entries: PosixACE[] | null;
}

export interface NfsV4ACE {
  principal: string;
  ace_type: "allow" | "deny" | "audit" | "alarm";
  flags: string[];
  mask: string[];
}

export interface NfsV4ACL {
  type: "nfsv4";
  entries: NfsV4ACE[];
}

export interface NtPrincipal {
  sid: string;
  // Null when the scanner couldn't translate the SID at scan time
  // (lookup failure, well-known SID without a friendly name, etc.).
  // Renderers fall back to either the resolver-map or a truncated SID.
  name: string | null;
}

export interface NtACE {
  sid: string;
  name: string | null;
  ace_type: "allow" | "deny" | "audit";
  flags: string[];
  mask: string[];
}

export interface NtACL {
  type: "nt";
  owner: NtPrincipal | null;
  group: NtPrincipal | null;
  control: string[];
  entries: NtACE[];
}

export interface S3Owner {
  id: string;
  display_name: string;
}

export interface S3Grant {
  grantee_type: "canonical_user" | "group" | "amazon_customer_by_email";
  grantee_id: string;
  grantee_name: string;
  permission: "FULL_CONTROL" | "READ" | "WRITE" | "READ_ACP" | "WRITE_ACP";
}

export interface S3ACL {
  type: "s3";
  owner: S3Owner | null;
  grants: S3Grant[];
}

// ---- Cloud-drive ACL (Drive / OneDrive / Dropbox) ----

export type CloudDrivePrincipalType = "user" | "group" | "anyone" | "domain";

export type CloudDriveRole =
  | "owner"
  | "writer"
  | "commenter"
  | "reader"
  | "file_organizer";

export type CloudDriveLinkScope = "anyone" | "domain" | "restricted";

export interface CloudDrivePrincipal {
  type: CloudDrivePrincipalType;
  id: string;
  email: string | null;
  name: string | null;
}

export interface CloudDriveLink {
  id: string;
  scope: CloudDriveLinkScope;
}

export interface CloudDriveGrant {
  principal: CloudDrivePrincipal;
  role: CloudDriveRole;
  link: CloudDriveLink | null;
  inherited: boolean;
  inherited_from_id: string | null;
  inherited_from_path: string | null;
}

export interface CloudDriveACL {
  type: "cloud_drive";
  grants: CloudDriveGrant[];
  domain_restricted_to: string | null;
}

export type ACL = PosixACL | NfsV4ACL | NtACL | S3ACL | CloudDriveACL;

export type EntryKind = "file" | "directory";

export interface BrowseChild {
  id: string;
  kind: EntryKind;
  name: string;
  path: string;
  extension: string | null;
  size_bytes: number | null;
  mime_type: string | null;
  content_hash: string | null;
  mode: number | null;
  owner_name: string | null;
  group_name: string | null;
  fs_modified_at: string | null;
  child_count: number | null;
}

export interface BrowseResponse {
  source_id: string;
  source_name: string;
  path: string;
  parent_path: string | null;
  is_root: boolean;
  entries: BrowseChild[];
  // v0.4.11 — cursor pagination. `next_cursor` is opaque (base64url
  // JSON); pass it back as `?cursor=...`. `null` means no more pages.
  // `total` is populated only on the first page (cursor=null) so the
  // footer can show "X of Y matched" without paying the count on
  // every subsequent page.
  next_cursor: string | null;
  total: number | null;
}

export interface EntryVersion {
  id: string;
  entry_id: string;
  scan_id: string | null;
  content_hash: string | null;
  size_bytes: number | null;
  mode: number | null;
  uid: number | null;
  gid: number | null;
  owner_name: string | null;
  group_name: string | null;
  acl: ACL | null;
  xattrs: Record<string, string> | null;
  detected_at: string;
}

// v0.6.0 — provider-specific metadata from Tier 3 self-hosted
// libraries (Paperless-ngx, Immich). Filesystem sources leave it null.
// Schemaless map; the Library Metadata renderer keys off well-known
// names (correspondent, document_type, person, album, …).
export type DomainMetadata = Record<string, unknown>;

export interface EntryDetail {
  id: string;
  // Nullable since v0.4.0 — see FileEntry.source_id.
  source_id: string | null;
  kind: EntryKind;
  parent_path: string;
  path: string;
  name: string;
  extension: string | null;
  size_bytes: number | null;
  mime_type: string | null;
  content_hash: string | null;
  mode: number | null;
  uid: number | null;
  gid: number | null;
  owner_name: string | null;
  group_name: string | null;
  acl: ACL | null;
  xattrs: Record<string, string> | null;
  domain_metadata: DomainMetadata | null;
  native_id: string | null;
  fs_created_at: string | null;
  fs_modified_at: string | null;
  fs_accessed_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  is_deleted: boolean;
  versions: EntryVersion[];
  source: {
    id: string;
    name: string;
    type: string;
    security_metadata: SourceSecurityMetadata | null;
  } | null;
  tags: EntryTagAssignment[];
}

export interface EntryTagAssignment {
  tag: string;
  inherited: boolean;
  inherited_from_path: string | null;
}

export interface LargestFile {
  id: string;
  filename: string;
  size_bytes: number;
  path: string;
  source_id: string;
  mime_type: string | null;
}

export interface Webhook {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
}

export interface RegisterRequest {
  username: string;
  email: string;
  password: string;
}

export type {
  PrincipalType,
  RightName,
  PrincipalRef,
  GroupRef,
  ACEReference,
  RightResult,
  EffectivePerms,
  EffectivePermsEvaluatedWith,
  EffectivePermsRequest,
} from "../lib/effectivePermsTypes";

export type {
  FsBinding,
  FsPerson,
  FsPersonInput,
  FsBindingInput,
  GroupsSource,
} from "../lib/identityTypes";

export type {
  SearchAsOverride,
  AuditEvent,
  AuditEventList,
} from "../lib/auditTypes";
