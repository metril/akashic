# go-smb2 vendor patch

**Base**: `github.com/hirochachacha/go-smb2 v1.1.0`
**Reason**: v1.1.0 does not expose a way to issue `SMB2 QUERY_INFO` with
`InfoType=SMB2_0_INFO_SECURITY`, so security descriptors cannot be fetched.

## Changes relative to v1.1.0

### `client.go`
- Added `(*Share).GetSecurityDescriptorBytes(name string) ([]byte, error)` — opens
  the file with `READ_CONTROL` access and calls `getSecurityDescriptorBytes()`.
- Added `(*File).getSecurityDescriptorBytes() ([]byte, error)` — issues
  `SMB2_QUERY_INFO` with `InfoType=SMB2_0_INFO_SECURITY` and
  `AdditionalInformation = OWNER_SECURITY_INFORMATION | GROUP_SECURITY_INFORMATION |
  DACL_SECURITY_INFORMATION` (0x7). Returns the raw, self-relative SD bytes as
  received from the server (MS-DTYP §2.4.6 format). SACL is intentionally omitted
  because querying it requires `SE_SECURITY_NAME` privilege which is rarely granted.

### `internal/smb2/const.go`
- Fixed typos in `AdditionalInformation` constants (`SECUIRTY` → `SECURITY`).
- Removed the duplicate commented-out `const` block that appeared at the end of the file.

### `internal/smb2/dtyp.go`
- Added `SecurityDescriptorDecoder` type with basic field accessors. This is a
  minimal decoder used to validate the SD header in tests; the full binary parsing
  is done by `scanner/internal/metadata/sddl`.

## Upstream reference

This patch is based on [hirochachacha/go-smb2 PR #65](https://github.com/hirochachacha/go-smb2/pull/65)
by `elimity-com` / `principis`, which adds higher-level parsed security info.
Our patch differs in that we return raw bytes (so the existing `sddl.ParseSecurityDescriptor`
pipeline is used unchanged) rather than the PR's `*FileSecurityInfo` struct.

## How to remove this vendor copy

1. Wait for upstream to merge a release that exposes a raw-bytes or equivalent API
   (track PR #65 or any successor).
2. Update `scanner/go.mod`: change `github.com/hirochachacha/go-smb2` version and
   **remove** the `replace` directive.
3. Update `scanner/internal/connector/smb.go`: change the `sdFetcher` interface to
   match whatever API the new release exposes (if different from `GetSecurityDescriptorBytes`).
4. Delete this directory (`scanner/internal/vendor/go-smb2/`).
