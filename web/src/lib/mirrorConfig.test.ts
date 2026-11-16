import { describe, expect, it } from "vitest";
import { validateMirrors, type MirrorMapping } from "./mirrorConfig";
import type { SourceType } from "../components/sources/sourceTypes";

const types = new Map<string, SourceType>([
  ["l", "local"],
  ["s", "s3"],
]);

const m = (o: Partial<MirrorMapping> = {}): MirrorMapping => ({
  source_id: "l",
  remote_prefix: "/data",
  local_prefix: "/mnt/immich",
  ...o,
});

describe("validateMirrors", () => {
  it("accepts valid immich and paperless lists", () => {
    expect(validateMirrors([m()], "immich")).toBeNull();
    expect(validateMirrors([m()], "immich", types)).toBeNull();
    expect(validateMirrors([m({ remote_prefix: "" })], "paperless", types)).toBeNull();
    expect(validateMirrors([], "immich")).toBeNull();
  });
  it("accepts an s3 backing local prefix without a leading slash", () => {
    expect(
      validateMirrors([m({ source_id: "s", local_prefix: "photos" })], "immich", types),
    ).toBeNull();
  });
  it("requires a backing source", () => {
    expect(validateMirrors([m({ source_id: "" })], "immich")).toBe("Pick a backing source");
  });
  it("requires an empty remote prefix for paperless", () => {
    expect(validateMirrors([m({ remote_prefix: "/data" })], "paperless")).toBe(
      "Paperless mappings use an empty remote prefix",
    );
  });
  it("requires immich remote prefix to start with /", () => {
    expect(validateMirrors([m({ remote_prefix: "data" })], "immich")).toBe(
      "Remote prefix must start with /",
    );
    expect(validateMirrors([m({ remote_prefix: "" })], "immich")).toBe(
      "Remote prefix must start with /",
    );
  });
  it("requires local prefix to start with / for known non-s3 backing", () => {
    expect(validateMirrors([m({ local_prefix: "mnt" })], "immich", types)).toBe(
      "Local prefix must start with /",
    );
  });
  it("rejects duplicates", () => {
    expect(validateMirrors([m(), m()], "immich")).toBe("Duplicate mapping");
  });
});
