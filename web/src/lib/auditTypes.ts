import type { PrincipalType } from "./effectivePermsTypes";

export interface SearchAsOverride {
  type: PrincipalType;
  identifier: string;
  groups: string[];
}

export interface AuditEvent {
  id: string;
  user_id: string | null;
  event_type: string;
  occurred_at: string;
  source_id: string | null;
  request_ip: string;
  user_agent: string;
  payload: Record<string, unknown>;
}

export interface AuditEventList {
  items: AuditEvent[];
  total: number;
  page: number;
  page_size: number;
}
