// Thin aliases over the generated OpenAPI schema (schema.d.ts). Never
// re-declare an API shape here: add an alias, or fix the spec.
import type { components } from "./schema";

type S = components["schemas"];

export type User = S["UserResponse"];
export type CurrentUser = S["MeResponse"];
export type Source = S["SourceResponse"];
export type Host = S["HostResponse"];
export type Scan = S["ScanResponse"];
export type ScanLogLine = S["LogEntryOut"];
export type Tag = S["TagResponse"];
export type Webhook = S["WebhookResponse"];
export type FileEntry = S["EntryResponse"];
export type SearchResult = S["SearchHit"];
export type SearchResults = S["SearchPage"];
export type DuplicateGroup = S["DuplicateGroup"];
export type StorageByType = S["StorageByType"];
export type StorageBySource = S["StorageBySource"];
export type LargestFile = S["LargestFile"];
export type BrowseChild = S["BrowseEntry"];
export type EffectiveCounts = S["EffectiveCounts"];
export type BrowseResponse = S["BrowsePage"];
export type EntryDetail = S["EntryDetailResponse"];
export type EntryVersion = S["EntryVersionResponse"];
export type EntryTagAssignment = S["EntryTagAssignment"];
export type ScopeInfo = S["ScopeInfo"];
export type ScopeCatalog = S["ScopeCatalog"];
export type ApiToken = S["ApiTokenResponse"];
export type ApiTokenCreated = S["ApiTokenCreated"];
export type AdminApiToken = S["AdminApiTokenResponse"];
export type ApiTokenCreate = S["ApiTokenCreate"];

export type PosixACE = S["PosixACE-Output"];
export type PosixACL = S["PosixACL-Output"];
export type NfsV4ACE = S["NfsV4ACE-Output"];
export type NfsV4ACL = S["NfsV4ACL-Output"];
export type NtPrincipal = S["NtPrincipal-Output"];
export type NtACE = S["NtACE-Output"];
export type NtACL = S["NtACL-Output"];
export type S3Owner = S["S3Owner-Output"];
export type S3Grant = S["S3Grant-Output"];
export type S3ACL = S["S3ACL-Output"];
export type CloudDriveACL = S["CloudDriveACL-Output"];
export type ACL = PosixACL | NfsV4ACL | NtACL | S3ACL | CloudDriveACL;
export type SourceListItem = S["SourceListResponse"];
export type ScanProgressEvent = S["WsScanProgress"];
export type ScanSnapshot = S["WsScanSnapshot"];
export type ScanLogEvent = S["WsScanLog"];
export type ScanWsEvent = ScanSnapshot | ScanProgressEvent | ScanLogEvent | S["WsPing"];
/** Frames on /ws/v1/scans (source and scan state). */
export type ScansStreamEvent =
  | S["WsScanListSnapshot"]
  | S["WsScanState"]
  | S["WsSourceCreated"]
  | S["WsSourceUpdated"]
  | S["WsSourceDeleted"]
  | S["WsHostChanged"]
  | S["WsPing"]
  | S["WsError"];
export type SnapshotScan = S["WsScanListEntry"];
export type PendingDiscoverySnapshot = S["WsPendingDiscovery"];
/** Frames on /ws/v1/scanners (scanner lifecycle, admin only). */
export type ScannersStreamEvent =
  | S["WsScannersSnapshot"]
  | S["WsScannerClaimRedeemed"]
  | S["WsScannerDiscoveryRequested"]
  | S["WsScannerDiscoveryApproved"]
  | S["WsScannerDiscoveryDenied"]
  | S["WsScannerDiscoveryExpired"]
  | S["WsScannerUpdated"]
  | S["WsScannerDeleted"]
  | S["WsPing"]
  | S["WsError"];
export type DuplicateFile = S["DuplicateFile"];
export type AuditEvent = S["AuditEventOut"];
export type FsPerson = S["FsPersonOut"];
export type FsPersonInput = S["FsPersonIn"];
export type FsBinding = S["FsBindingOut"];
export type FsBindingInput = S["FsBindingIn"];
export type PrincipalRef = S["PrincipalRef-Input"];
export type GroupRef = S["GroupRef-Input"];
export type PrincipalType = PrincipalRef["type"];
export type GroupsSource = FsBinding["groups_source"];
export type ACEReference = S["ACEReference"];
export type RightResult = S["RightResult"];
export type EffectivePerms = S["EffectivePerms"];
export type EffectivePermsRequest = S["EffectivePermsRequest"];
export type RightName = keyof EffectivePerms["rights"];
export type DomainMetadata = Record<string, unknown>;
export type CloudDriveGrant = S["CloudDriveGrant-Output"];
export type CloudDriveRole = CloudDriveGrant["role"];
export type SearchAsOverride = { type: PrincipalType; identifier: string; groups: string[] };
export type ACLType = ACL["type"];
export type SourceSecurityMetadata = S["SourceSecurityMetadata-Output"];
export type PublicAccessBlock = S["PublicAccessBlock"];

/** Cursor-paginated list envelope (`Page[T]` in the API; the spec only
 *  carries concrete `Page_X_` instances, so the generic lives here). */
export interface Paginated<T> {
  items: T[];
  next_cursor: string | null;
  limit: number;
  total?: number | null;
}
export type StorageByLinkRole = S["StorageByLinkRole"];
export type BackingFileRef = S["BackingFileRef"];
export type LinkedEntryRef = S["LinkedEntryRef"];
