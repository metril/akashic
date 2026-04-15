export interface User {
  id: number;
  username: string;
  email: string;
  created_at: string;
}

export interface Source {
  id: number;
  name: string;
  path: string;
  source_type: string;
  enabled: boolean;
  last_scan_at: string | null;
  created_at: string;
  file_count?: number;
  total_size?: number;
}

export interface FileVersion {
  id: number;
  file_id: number;
  size: number;
  hash: string;
  scanned_at: string;
}

export interface FileLocation {
  id: number;
  file_id: number;
  source_id: number;
  path: string;
  source?: Source;
}

export interface FileEntry {
  id: number;
  name: string;
  extension: string | null;
  size: number;
  hash: string;
  mime_type: string | null;
  created_at: string | null;
  modified_at: string | null;
  scanned_at: string;
  source_id: number;
  path: string;
  source?: Source;
  tags?: Tag[];
}

export interface SearchResult {
  id: number;
  name: string;
  extension: string | null;
  size: number;
  hash: string;
  mime_type: string | null;
  path: string;
  source_id: number;
  scanned_at: string;
}

export interface SearchResults {
  results: SearchResult[];
  total: number;
  query: string;
}

export interface DuplicateGroup {
  hash: string;
  file_count: number;
  total_size: number;
  wasted_size: number;
  files?: FileEntry[];
}

export interface Scan {
  id: number;
  source_id: number;
  status: "pending" | "running" | "completed" | "failed";
  started_at: string | null;
  completed_at: string | null;
  files_scanned: number;
  files_added: number;
  files_updated: number;
  files_removed: number;
  error_message: string | null;
  source?: Source;
}

export interface Tag {
  id: number;
  name: string;
  color: string | null;
  created_at: string;
}

export interface StorageByType {
  extension: string;
  file_count: number;
  total_size: number;
}

export interface StorageBySource {
  source_id: number;
  source_name: string;
  file_count: number;
  total_size: number;
}

export interface LargestFile {
  id: number;
  name: string;
  size: number;
  path: string;
  source_id: number;
  extension: string | null;
}

export interface Webhook {
  id: number;
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
