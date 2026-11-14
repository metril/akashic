import { describe, expect, it } from "vitest";
import { buildPatch, draftFromOverrides, validateDraft } from "./ScannerConfigPanel";

const none = { tika_url: null, extract_workers: null, max_concurrent_units: null };

describe("scanner config helpers", () => {
  it("validates ranges and url", () => {
    const e = validateDraft({ tika_url: "ftp://x", extract_workers: "65", max_concurrent_units: "0" });
    expect(e.tika_url).toBeTruthy();
    expect(e.extract_workers).toBeTruthy();
    expect(e.max_concurrent_units).toBeTruthy();
    expect(validateDraft({ tika_url: "", extract_workers: "4", max_concurrent_units: null })).toEqual({});
  });
  it("builds patch with clear flags", () => {
    const o = { tika_url: "http://t", extract_workers: 8, max_concurrent_units: null };
    const d = { ...draftFromOverrides(o), tika_url: null, max_concurrent_units: "3" };
    expect(buildPatch(d, o)).toEqual({ clear_tika_url: true, max_concurrent_units: 3 });
    expect(buildPatch({ ...draftFromOverrides(none), tika_url: "" }, none)).toEqual({ tika_url: "" });
  });
});
