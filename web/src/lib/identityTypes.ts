import type { PrincipalType } from "./effectivePermsTypes";

export type GroupsSource = "manual" | "auto";

export interface FsBinding {
  id: string;
  fs_person_id: string;
  source_id: string;
  identity_type: PrincipalType;
  identifier: string;
  groups: string[];
  groups_source: GroupsSource;
  groups_resolved_at: string | null;
  created_at: string;
}

export interface FsPerson {
  id: string;
  user_id: string;
  label: string;
  is_primary: boolean;
  created_at: string;
  bindings: FsBinding[];
}

export interface FsPersonInput {
  label: string;
  is_primary?: boolean;
}

export interface FsBindingInput {
  source_id: string;
  identity_type: PrincipalType;
  identifier: string;
  groups: string[];
}
