export interface User {
  id: string;
  username: string;
  email: string;
  created_at: string;
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
  source?: Source;
}

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
  count: number;
  total_size: number;
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
