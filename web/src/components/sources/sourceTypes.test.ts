import { describe, expect, it } from "vitest";
import { validateSourceConfig, validateMirrorConfig, type SourceType } from "./sourceTypes";

const types = new Map<string, SourceType>([
  ["im", "immich"],
  ["pl", "paperless"],
]);

describe("validateSourceConfig mirror_of gate", () => {
  it("rejects an immich mapping with an empty remote prefix", () => {
    const cfg = { path: "/x", mirror_of: [{ source_id: "im", remote_prefix: "", local_prefix: "/m" }] };
    expect(validateSourceConfig("local", cfg, types)).toBe("Remote prefix must start with /");
  });
  it("rejects a paperless mapping with a non-empty remote prefix", () => {
    const cfg = { path: "/x", mirror_of: [{ source_id: "pl", remote_prefix: "/d", local_prefix: "/m" }] };
    expect(validateSourceConfig("local", cfg, types)).toBe("Paperless mappings use an empty remote prefix");
  });
  it("host-backed gate agrees", () => {
    const cfg = { mirror_of: [{ source_id: "pl", remote_prefix: "/d", local_prefix: "/m" }] };
    expect(validateMirrorConfig("nfs", cfg, types)).toBe("Paperless mappings use an empty remote prefix");
  });
});
