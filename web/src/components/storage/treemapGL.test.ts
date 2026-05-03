import { describe, expect, it } from "vitest";

import { parseColor } from "./treemapGL";

describe("parseColor", () => {
  it("parses 6-digit hex", () => {
    const c = parseColor("#ff8000");
    expect(c[0]).toBeCloseTo(1);
    expect(c[1]).toBeCloseTo(128 / 255);
    expect(c[2]).toBeCloseTo(0);
    expect(c[3]).toBe(1);
  });

  it("parses 3-digit hex (expands)", () => {
    const c = parseColor("#f80");
    expect(c[0]).toBeCloseTo(255 / 255);
    expect(c[1]).toBeCloseTo(136 / 255);
    expect(c[2]).toBeCloseTo(0);
  });

  it("parses rgb()", () => {
    const c = parseColor("rgb(10, 20, 30)");
    expect(c[0]).toBeCloseTo(10 / 255);
    expect(c[1]).toBeCloseTo(20 / 255);
    expect(c[2]).toBeCloseTo(30 / 255);
    expect(c[3]).toBe(1);
  });

  it("parses rgba()", () => {
    const c = parseColor("rgba(10, 20, 30, 0.5)");
    expect(c[3]).toBeCloseTo(0.5);
  });

  it("returns transparent on garbage input", () => {
    expect(parseColor("not a color")).toEqual([0, 0, 0, 0]);
    expect(parseColor("#zzzz")).toEqual([0, 0, 0, 0]);
    expect(parseColor("rgb(broken")).toEqual([0, 0, 0, 0]);
  });

  it("ignores whitespace", () => {
    const c = parseColor("  rgb( 10 , 20 , 30 )  ");
    expect(c[0]).toBeCloseTo(10 / 255);
  });
});
