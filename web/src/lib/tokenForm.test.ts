import { describe, expect, it } from "vitest";
import { initialTokenForm, tokenFormReducer, toCreateRequest, type TokenFormAction } from "./tokenForm";

const viewer = new Set(["files:read", "files:content", "tags:read", "sources:read", "tokens:read", "tokens:write", "webhooks:write"]);
const admin = new Set([...viewer, "sources:write", "scans:write", "tags:write", "users:write", "settings:read", "data:purge", "tokens:admin"]);

const run = (...actions: TokenFormAction[]) => actions.reduce(tokenFormReducer, initialTokenForm);

describe("presets", () => {
  it("Read-only is every :read scope the user has plus files:content", () => {
    const s = run({ type: "preset", preset: "read-only", available: viewer });
    expect([...s.scopes].sort()).toEqual(["files:content", "files:read", "sources:read", "tags:read", "tokens:read"]);
  });
  it("Read-only never offers scopes the user lacks", () => {
    const s = run({ type: "preset", preset: "read-only", available: new Set(["files:read"]) });
    expect([...s.scopes]).toEqual(["files:read"]);
  });
  it("Full is all of the user's scopes", () => {
    expect(run({ type: "preset", preset: "full", available: viewer }).scopes).toEqual(viewer);
    expect(run({ type: "preset", preset: "full", available: admin }).scopes).toEqual(admin);
  });
});

describe("source limit", () => {
  it("selecting sources clears scopes a limited token can't hold", () => {
    const s = run(
      { type: "preset", preset: "full", available: admin },
      { type: "sources", sourceIds: ["s1"] },
    );
    expect([...s.scopes].sort()).toEqual([
      "files:content", "files:read", "scans:write", "settings:read", "sources:read", "sources:write", "tags:read", "tags:write", "tokens:read",
    ]);
    expect(s.scopes.has("webhooks:write")).toBe(false);
    expect(s.scopes.has("data:purge")).toBe(false);
    expect(s.scopes.has("tokens:write")).toBe(false);
  });
  it("disallowed scopes can't be toggled on while limited, and can again once cleared", () => {
    const limited = run({ type: "sources", sourceIds: ["s1"] }, { type: "toggle", scope: "users:write" });
    expect(limited.scopes.has("users:write")).toBe(false);
    const ok = run({ type: "sources", sourceIds: ["s1"] }, { type: "toggle", scope: "tags:write" });
    expect(ok.scopes.has("tags:write")).toBe(true);
    const cleared = tokenFormReducer(limited, { type: "sources", sourceIds: [] });
    expect(tokenFormReducer(cleared, { type: "toggle", scope: "users:write" }).scopes.has("users:write")).toBe(true);
  });
  it("presets respect an active limit", () => {
    const s = run({ type: "sources", sourceIds: ["s1"] }, { type: "preset", preset: "full", available: admin });
    expect(s.scopes.has("users:write")).toBe(false);
    expect(s.scopes.has("tags:write")).toBe(true);
  });
});

describe("toCreateRequest", () => {
  const now = new Date("2026-01-01T00:00:00Z");
  it("sends sources and expiry only when chosen", () => {
    const s = run({ type: "name", name: " ci " }, { type: "toggle", scope: "files:read" }, { type: "expiry", expiry: "30" }, { type: "sources", sourceIds: ["s1"] });
    expect(toCreateRequest(s, now)).toEqual({
      name: "ci", scopes: ["files:read"], source_ids: ["s1"], expires_at: "2026-01-31T00:00:00.000Z",
    });
    const open = run({ type: "name", name: "x" }, { type: "toggle", scope: "files:read" }, { type: "expiry", expiry: "never" });
    expect(toCreateRequest(open, now)).toEqual({ name: "x", scopes: ["files:read"] });
  });
});
