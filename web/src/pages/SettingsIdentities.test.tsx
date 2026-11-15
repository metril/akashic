import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  ok: (x: unknown) => x,
}));

import { BindingSourceBadge, UnboundPanel } from "./SettingsIdentities";

const row = (identity_type: string, identifier: string, confidence: string) => ({
  id: identifier, user_id: "u", identity_type, identifier, confidence,
  groups: [], first_seen_at: "", last_seen_at: "",
}) as never;

describe("UnboundPanel", () => {
  it("explains name-only identities", () => {
    const html = renderToStaticMarkup(<UnboundPanel rows={[row("posix_uid", "-1", "name")]} />);
    expect(html).toContain("group names only");
    expect(html).toContain("SMB sources at login");
    expect(html).not.toContain("principal_domain");
  });
  it("blames principal_domain for sid rows", () => {
    const html = renderToStaticMarkup(<UnboundPanel rows={[row("sid", "S-1-5-21-1", "claim")]} />);
    expect(html).toContain("principal_domain");
    expect(html).not.toContain("group names only");
  });
  it("badges smb provenance", () => {
    expect(renderToStaticMarkup(<BindingSourceBadge source="smb" />)).toContain("smb");
  });
});
