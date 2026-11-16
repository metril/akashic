import { describe, expect, it } from "vitest";
import { validateSourceConfig, type SourceType } from "./sourceTypes";

const types = new Map<string, SourceType>([["lo", "local"]]);
const base = { url: "http://x", api_key: "k", api_token: "t" };

describe("validateSourceConfig mirrors gate", () => {
  it("rejects a paperless mapping with a non-empty remote prefix", () => {
    const cfg = { ...base, mirrors: [{ source_id: "lo", remote_prefix: "/d", local_prefix: "/m" }] };
    expect(validateSourceConfig("paperless", cfg, types)).toBe(
      "Paperless mappings use an empty remote prefix",
    );
  });
  it("rejects an immich mapping with an empty remote prefix", () => {
    const cfg = { ...base, mirrors: [{ source_id: "lo", remote_prefix: "", local_prefix: "/m" }] };
    expect(validateSourceConfig("immich", cfg, types)).toBe("Remote prefix must start with /");
  });
  it("accepts a valid immich mapping", () => {
    const cfg = { ...base, mirrors: [{ source_id: "lo", remote_prefix: "/d", local_prefix: "/m" }] };
    expect(validateSourceConfig("immich", cfg, types)).toBeNull();
  });
  it("does not validate mirrors on backing types", () => {
    expect(validateSourceConfig("local", { path: "/x" }, types)).toBeNull();
  });
});
