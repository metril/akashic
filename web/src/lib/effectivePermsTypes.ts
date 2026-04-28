export type PrincipalType = "posix_uid" | "sid" | "nfsv4_principal" | "s3_canonical";
export type RightName = "read" | "write" | "execute" | "delete" | "change_perms";

export interface PrincipalRef {
  type: PrincipalType;
  identifier: string;
  name?: string;
}

export interface GroupRef {
  type: PrincipalType;
  identifier: string;
  name?: string;
}

export interface ACEReference {
  ace_index: number;
  summary: string;
}

export interface RightResult {
  granted: boolean;
  by: ACEReference[];
}

export interface EffectivePermsEvaluatedWith {
  model: "posix" | "nfsv4" | "nt" | "s3" | "none";
  principal: PrincipalRef;
  groups: GroupRef[];
  caveats: string[];
}

export interface EffectivePerms {
  rights: Record<RightName, RightResult>;
  evaluated_with: EffectivePermsEvaluatedWith;
}

export interface EffectivePermsRequest {
  principal: PrincipalRef;
  groups?: GroupRef[];
  principal_name_hint?: string;
}
