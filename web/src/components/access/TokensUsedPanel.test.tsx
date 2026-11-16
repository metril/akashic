import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn() },
  ok: (x: unknown) => x,
}));

import { PrincipalToFilesResults } from "./AccessResults";
import { TokensUsedPanel } from "./TokensUsedPanel";

const tokens = [
  { token: "sid:S-1-5-21-1", name: "Alice", origin: "ad" },
  { token: "posix:uid:1001", name: null, origin: "akashic_binding" },
  { token: "*", name: null, origin: "builtin" },
] as never;
const exp = (over: Record<string, unknown> = {}) =>
  ({ status: "ok", cached_at: null, detail: null, missing_identity_kinds: [], ...over }) as never;
const identity = { id: "sid:S-1-5-21-1" } as never;
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
  it("shows cached as-of time", () => {
    const html = panel(exp({ status: "cached", cached_at: "2026-11-16T10:00:00Z" }));
    expect(html).toContain("as of");
    expect(html).toContain("2026-11-16T10:00:00Z");
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
