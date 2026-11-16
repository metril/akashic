import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn() },
  ok: (x: unknown) => x,
}));

import { PrincipalToFilesResults } from "./AccessResults";
import { formatDateTime } from "../../lib/format";
import { canRefresh, TokensUsedPanel } from "./TokensUsedPanel";

const tokens = [
  { token: "sid:S-1-5-21-1", name: "Alice", origin: "ad" },
  { token: "posix:uid:1001", name: null, origin: "akashic_binding" },
  { token: "*", name: null, origin: "builtin" },
] as never;
const exp = (over: Record<string, unknown> = {}) =>
  ({ status: "ok", cached_at: null, detail: null, missing_identity_kinds: [], ...over }) as never;
const identity = { id: "sid:S-1-5-21-1", kind: "person", backends: [{ backend: "ad", ref: "g" }] } as never;
const panel = (e: never, id: never = null as never) =>
  renderToStaticMarkup(
    <TokensUsedPanel tokens={tokens} expansion={e} identity={id} onRefresh={() => {}} refreshing={false} />,
  );

describe("TokensUsedPanel", () => {
  it("lists each token with its origin label", () => {
    const html = panel(exp());
    for (const s of ["sid:S-1-5-21-1", "posix:uid:1001", "Active Directory", "Akashic binding", "Built-in"])
      expect(html).toContain(s);
  });
  it("shows partial expansion with detail", () => {
    const html = panel(exp({ status: "partial", detail: "tokenGroups read failed" }));
    expect(html).toContain("Group expansion incomplete");
    expect(html).toContain("tokenGroups read failed");
  });
  it("shows failed expansion as an amber warning with the built-in-only message", () => {
    const html = panel(exp({ status: "failed", detail: "no file-system identity" }));
    expect(html).toContain("border-amber-300");
    expect(html).toContain(
      "No file-system identity; results show only what everyone, authenticated users and email shares reach.",
    );
  });
  it("shows cached as-of time", () => {
    const html = panel(exp({ status: "cached", cached_at: "2026-11-16T10:00:00Z" }));
    expect(html).toContain("as of");
    expect(html).toContain(formatDateTime("2026-11-16T10:00:00Z"));
  });
  it("renders missing identity kinds", () => {
    const html = panel(exp({ missing_identity_kinds: ["sid", "posix"] }));
    expect(html).toContain("No SID");
    expect(html).toContain("No POSIX id");
  });
  it("shows Refresh only when an identity is given", () => {
    expect(panel(exp(), identity)).toContain("Refresh");
    expect(panel(exp())).not.toContain("Refresh");
  });
  it("hides Refresh for groups and identities without a directory record", () => {
    const group = { id: "g", kind: "group", backends: [{ backend: "ad", ref: "g" }] } as never;
    const akashicOnly = { id: "a", kind: "person", backends: [{ backend: "akashic", ref: "u" }] } as never;
    expect(panel(exp(), group)).not.toContain("Refresh");
    expect(panel(exp(), akashicOnly)).not.toContain("Refresh");
    expect(canRefresh(group)).toBe(false);
    expect(canRefresh(akashicOnly)).toBe(false);
    expect(canRefresh(null)).toBe(false);
    expect(canRefresh(identity)).toBe(true);
  });
  it("shows the refresh error inline", () => {
    const html = renderToStaticMarkup(
      <TokensUsedPanel tokens={tokens} expansion={exp()} identity={identity} refreshError="all directory reads failed" />,
    );
    expect(html).toContain("all directory reads failed");
  });
});

describe("PrincipalToFilesResults", () => {
  const data = (deny: boolean) =>
    ({
      principal: { token: "sid:S-1", name: null, domain: null, kind: "user" },
      right: "read",
      summary: { file_count: 1, total_size_bytes: 1, source_count: 1 },
      by_source: [],
      sample: [{ id: "e1", source_id: "s", path: "/a", filename: "a.txt", size_bytes: 1, has_deny_ace: deny }],
      next_cursor: null, limit: 20,
      warnings: ["Deny ACEs are not evaluated for group-applied access"],
    }) as never;
  it("shows deny ACE badge and warnings", () => {
    const html = renderToStaticMarkup(<PrincipalToFilesResults data={data(true)} onSelectEntry={() => {}} />);
    expect(html).toContain("deny ACE");
    expect(html).toContain("Deny ACEs are not evaluated");
  });
  it("omits the badge when no deny ACE", () => {
    const html = renderToStaticMarkup(<PrincipalToFilesResults data={data(false)} onSelectEntry={() => {}} />);
    expect(html).not.toContain(">deny ACE<");
  });
});
