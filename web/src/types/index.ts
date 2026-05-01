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
  connection_config: Record<string, unknown>;
  scan_schedule: string | null;
  exclude_patterns: string[] | null;
  last_scan_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  security_metadata?: SourceSecurityMetadata | null;
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
  source_id: string;
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
  source_id: string;
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
  source_id: string;
  fs_modified_at: number | null;
  tags: string[];
}

export interface SearchResults {
  results: SearchResult[];
  total: number;
  query: string;
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
  source?: Source;
}

export interface ScanLogLine {
  id: string;
  ts: string;
  level: "info" | "warn" | "error" | "stderr";
  message: string;
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

export type ACLType = "posix" | "nfsv4" | "nt" | "s3";

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
  name: string;
}

export interface NtACE {
  sid: string;
  name: string;
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

export type ACL = PosixACL | NfsV4ACL | NtACL | S3ACL;

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

export interface EntryDetail {
  id: string;
  source_id: string;
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
