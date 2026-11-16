import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn() },
  ok: (x: unknown) => x,
}));

import { BackendStatusChips, IdentityResultRow } from "./IdentityPicker";

const result = (over: Record<string, unknown> = {}) =>
  ({
    id: "sid:S-1-5-21-1", kind: "person", display_name: "Alice A", username: "alice",
    email: "a@x.io", dn: null, sid: "S-1-5-21-1", akashic_user_id: null,
    backends: [
      { backend: "ad", ref: "g1" },
      { backend: "authentik", ref: "7" },
      { backend: "akashic", ref: "u1" },
    ],
    has_fs_identity: true, match_quality: "exact", possible_match_of: [],
    groups_synced_at: null, ...over,
  }) as never;

const row = (r: never) => renderToStaticMarkup(<IdentityResultRow result={r} onPick={() => {}} />);

describe("IdentityResultRow", () => {
  it("renders a badge per backend as a button", () => {
    const html = row(result());
    expect(html).toContain("<button");
    for (const b of ["AD", "Authentik", "akashic"]) expect(html).toContain(`>${b}<`);
  });
  it("flags possible matches only", () => {
    expect(row(result({ match_quality: "possible" }))).toContain("possible match");
    expect(row(result())).not.toContain("possible match");
  });
  it("flags identities without a file-system identity", () => {
    expect(row(result({ has_fs_identity: false }))).toContain("no file-system identity");
    expect(row(result())).not.toContain("no file-system identity");
  });
});

describe("BackendStatusChips", () => {
  it("shows not configured and unavailable detail", () => {
    const html = renderToStaticMarkup(
      <BackendStatusChips
        statuses={[
          { backend: "ad", status: "not_configured", detail: null },
          { backend: "authentik", status: "unavailable", detail: "timeout after 3s" },
        ]}
      />,
    );
    expect(html).toContain("not configured");
    expect(html).toContain("timeout after 3s");
  });
});
