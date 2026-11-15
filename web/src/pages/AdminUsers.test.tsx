import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../api/client", () => ({
  client: { GET: vi.fn(), POST: vi.fn(), PATCH: vi.fn(), DELETE: vi.fn() },
  ok: (x: unknown) => x,
}));

import { UserRow } from "./AdminUsers";

const user = (over: Record<string, unknown> = {}) => ({
  id: "u1", username: "alice", email: "a@x.io", role: "viewer", role_pinned: false,
  auth_provider: "oidc", created_at: "", last_login_at: null, ...over,
}) as never;

const render = (u: never, canWrite: boolean, error?: string) =>
  renderToStaticMarkup(
    <table><tbody>
      <UserRow user={u} canWrite={canWrite} busy={false} error={error} onRole={() => {}} onUnpin={() => {}} />
    </tbody></table>,
  );

describe("UserRow", () => {
  it("shows a role select only with users:write", () => {
    expect(render(user(), true)).toContain("<select");
    expect(render(user(), false)).not.toContain("<select");
  });
  it("shows pinned badge and the IdP action for pinned IdP users", () => {
    const html = render(user({ role_pinned: true }), true);
    expect(html).toContain("pinned");
    expect(html).toContain("Return to IdP control");
  });
  it("hides the IdP action for local or unpinned users", () => {
    expect(render(user({ role_pinned: true, auth_provider: "local" }), true)).not.toContain("Return to IdP control");
    expect(render(user(), true)).not.toContain("Return to IdP control");
  });
  it("shows the 409 error inline", () => {
    expect(render(user(), true, "Cannot demote the last admin")).toContain("Cannot demote the last admin");
  });
});
