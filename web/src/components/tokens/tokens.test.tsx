import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  isAuthenticated: () => true,
  setToken: vi.fn(),
  clearToken: vi.fn(),
}));

import { ScopePicker } from "./ScopePicker";
import { TokenReveal } from "./TokenReveal";
import { CreateTokenDialog } from "./CreateTokenDialog";
import ScopeRoute from "../ScopeRoute";
import { Sidebar } from "../Sidebar";
import Settings from "../../pages/Settings";
import { groupScopes } from "../../lib/scopes";
import type { CurrentUser, ScopeCatalog } from "../../types";

const catalog: ScopeCatalog = {
  scopes: [
    { name: "files:read", description: "Search and read files.", read_only: true, allowed_with_source_limit: true },
    { name: "sources:write", description: "Change sources.", read_only: false, allowed_with_source_limit: true },
    { name: "webhooks:write", description: "Manage webhooks.", read_only: false, allowed_with_source_limit: false },
    { name: "settings:write", description: "Change settings.", read_only: false, allowed_with_source_limit: false },
  ],
  roles: {},
};
const VIEWER = ["files:read", "webhooks:write", "tokens:read", "tokens:write", "identities:read", "sources:read"];
const ADMIN = [...VIEWER, "sources:write", "settings:write", "settings:read", "audit:read", "access:read", "health:read",
  "maintenance:write", "credentials:read", "scanners:read", "tags:write", "tokens:admin"];

function me(scopes: string[]): CurrentUser {
  return { id: "u", username: "u", email: null, role: "x", created_at: "", scopes };
}

function withApp(ui: React.ReactElement, scopes: string[], withCatalog = true) {
  const qc = new QueryClient();
  qc.setQueryData(["users", "me"], me(scopes));
  if (withCatalog) qc.setQueryData(["scopes"], catalog);
  qc.setQueryData(["sources"], [{ id: "s1", name: "Photos" }]);
  return renderToStaticMarkup(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ScopePicker", () => {
  const groups = groupScopes(catalog.scopes, new Set(["files:read", "sources:write", "webhooks:write"]));
  const render = (limited: boolean) =>
    renderToStaticMarkup(<ScopePicker groups={groups} selected={new Set()} onToggle={() => {}} sourceLimited={limited} />);

  it("groups by resource with descriptions and only the offered scopes", () => {
    const html = render(false);
    expect(html).toContain("Search and read files.");
    expect(html).not.toContain("settings:write");
    expect(html).not.toMatch(/disabled/);
  });
  it("disables scopes a limited token can't hold, with a hint", () => {
    const html = render(true);
    expect(html).toMatch(/aria-label="webhooks:write"[^>]*disabled|disabled[^>]*aria-label="webhooks:write"/);
    expect(html).not.toMatch(/disabled[^>]*aria-label="sources:write"|aria-label="sources:write"[^>]*disabled/);
    expect(html).toContain("write scopes on other resources");
  });
});

describe("CreateTokenDialog", () => {
  it("offers only the user's own scopes and both presets", () => {
    const html = withApp(<CreateTokenDialog onClose={() => {}} />, VIEWER);
    expect(html).toContain("Read-only");
    expect(html).toContain("Full");
    expect(html).toContain("webhooks:write");
    expect(html).not.toContain("settings:write");
    expect(html).toContain("Source Photos");
  });
});

describe("CreateTokenDialog without a catalog", () => {
  it("disables presets, sources and submit while loading", () => {
    const html = withApp(<CreateTokenDialog onClose={() => {}} />, VIEWER, false);
    expect(html).toContain("Loading scopes");
    for (const label of ["Read-only", "Full", "Create token"]) {
      expect(html).toMatch(new RegExp(`<button[^>]*disabled[^>]*>(<[^>]*>)*${label}`));
    }
    expect(html).toMatch(/aria-label="Source Photos"[^>]*disabled|disabled[^>]*aria-label="Source Photos"/);
  });
});

describe("TokenReveal", () => {
  it("shows the secret once with a won't-be-shown-again warning", () => {
    const html = renderToStaticMarkup(<TokenReveal token="akp_secret123" onDone={() => {}} />);
    expect(html).toContain("akp_secret123");
    expect(html).toContain("won&#x27;t be shown again");
    expect(html).toContain("Copy");
  });
});

describe("scope gating", () => {
  const sidebar = (scopes: string[]) => withApp(<Sidebar mobileOpen={false} onMobileClose={() => {}} />, scopes);
  it("hides the admin nav from a viewer and shows it to an admin", () => {
    expect(sidebar(VIEWER)).not.toContain("Audit log");
    expect(sidebar(VIEWER)).not.toContain("Maintenance");
    expect(sidebar(ADMIN)).toContain("Audit log");
    expect(sidebar(ADMIN)).toContain("Maintenance");
  });
  it("shows settings tabs by scope", () => {
    const viewer = withApp(<Settings />, VIEWER);
    expect(viewer).toContain("Identities");
    expect(viewer).toContain("API tokens");
    expect(viewer).not.toContain("Credentials");
    expect(viewer).not.toContain("Server");
    const admin = withApp(<Settings />, ADMIN);
    expect(admin).toContain("Credentials");
    expect(admin).toContain("Tags");
  });
  it("renders a gated route only with the scope", () => {
    const page = (scopes: string[]) =>
      withApp(<ScopeRoute scope="audit:read"><p>audit page</p></ScopeRoute>, scopes);
    expect(page(VIEWER)).not.toContain("audit page");
    expect(page(ADMIN)).toContain("audit page");
  });
});
