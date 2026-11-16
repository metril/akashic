import { describe, expect, it } from "vitest";
import { validateMirrorOf, type MirrorMapping } from "./mirrorConfig";
import type { SourceType } from "../components/sources/sourceTypes";

const types = new Map<string, SourceType>([
  ["a", "immich"],
  ["p", "paperless"],
]);

const m = (o: Partial<MirrorMapping> = {}): MirrorMapping => ({
  source_id: "a",
  remote_prefix: "/data",
  local_prefix: "/mnt/immich",
  ...o,
});

describe("validateMirrorOf", () => {
  it("accepts valid immich and paperless lists", () => {
    expect(validateMirrorOf([m()], "local")).toBeNull();
    expect(validateMirrorOf([m()], "local", types)).toBeNull();
    expect(
      validateMirrorOf([m({ source_id: "p", remote_prefix: "" })], "nfs", types),
    ).toBeNull();
    expect(validateMirrorOf([], "local")).toBeNull();
  });
  it("requires a target", () => {
    expect(validateMirrorOf([m({ source_id: "" })], "local")).toBe("Pick a target source");
  });
  it("requires remote prefix to start with / (when non-empty)", () => {
    expect(validateMirrorOf([m({ remote_prefix: "data" })], "local")).toBe(
      "Remote prefix must start with /",
    );
    expect(validateMirrorOf([m({ remote_prefix: "" })], "local", types)).toBe(
      "Remote prefix must start with /",
    );
  });
  it("requires empty remote prefix for paperless", () => {
    expect(
      validateMirrorOf([m({ source_id: "p", remote_prefix: "/data" })], "local", types),
    ).toBe("Paperless mappings use an empty remote prefix");
  });
  it("requires local prefix to start with / except s3", () => {
    expect(validateMirrorOf([m({ local_prefix: "mnt" })], "smb")).toBe(
      "Local prefix must start with /",
    );
    expect(validateMirrorOf([m({ local_prefix: "photos/" })], "s3")).toBeNull();
  });
  it("rejects duplicates", () => {
    expect(validateMirrorOf([m(), m()], "local")).toBe("Duplicate mapping");
  });
});
