import { describe, expect, it } from "vitest";

import {
  IDENTITY,
  SCALE_MAX,
  SCALE_MIN,
  clamp,
  screenToWorld,
  zoomAt,
} from "./treemapViewport";

const W = 1000;
const H = 800;

describe("clamp", () => {
  it("identity passes through", () => {
    expect(clamp(IDENTITY, W, H)).toEqual(IDENTITY);
  });

  it("scale floor enforced", () => {
    const v = clamp({ tx: 0, ty: 0, scale: 0.1 }, W, H);
    expect(v.scale).toBe(SCALE_MIN);
  });

  it("scale ceiling enforced", () => {
    const v = clamp({ tx: 0, ty: 0, scale: 100 }, W, H);
    expect(v.scale).toBe(SCALE_MAX);
  });

  it("translate kept within visible bounds at scale=1", () => {
    // At identity scale, max pan is (w*0.25, h*0.25) on each side.
    expect(clamp({ tx: 1000, ty: 0, scale: 1 }, W, H).tx).toBe(W * 0.25);
    expect(clamp({ tx: -1000, ty: 0, scale: 1 }, W, H).tx).toBe(
      -(W * 1 - W * 0.25),
    );
  });

  it("translate bounds expand with scale", () => {
    // At scale=2, you can pan further left because the rendered width
    // is 2x the container. Push past the bound to verify clamping.
    const v = clamp({ tx: -5000, ty: 0, scale: 2 }, W, H);
    expect(v.tx).toBe(-(W * 2 - W * 0.25));
  });
});

describe("zoomAt", () => {
  it("scales by factor", () => {
    const v = zoomAt(IDENTITY, 500, 400, 2);
    expect(v.scale).toBe(2);
  });

  it("respects scale ceiling", () => {
    const v = zoomAt({ tx: 0, ty: 0, scale: SCALE_MAX }, 0, 0, 10);
    expect(v.scale).toBe(SCALE_MAX);
  });

  it("respects scale floor", () => {
    const v = zoomAt({ tx: 0, ty: 0, scale: SCALE_MIN }, 0, 0, 0.1);
    expect(v.scale).toBe(SCALE_MIN);
  });

  it("preserves the world point under the cursor (zoom-at-cursor)", () => {
    // At identity, screen (200, 300) corresponds to world (200, 300).
    // After zoom by 2x at (200, 300), the same world point (200, 300)
    // must still map to screen (200, 300).
    const v = zoomAt(IDENTITY, 200, 300, 2);
    const w = screenToWorld(v, 200, 300);
    expect(w.x).toBeCloseTo(200, 5);
    expect(w.y).toBeCloseTo(300, 5);
  });
});

describe("screenToWorld", () => {
  it("identity is the inverse of itself", () => {
    expect(screenToWorld(IDENTITY, 123, 456)).toEqual({ x: 123, y: 456 });
  });

  it("inverts pan", () => {
    expect(screenToWorld({ tx: 50, ty: 100, scale: 1 }, 200, 300)).toEqual({
      x: 150,
      y: 200,
    });
  });

  it("inverts scale", () => {
    expect(screenToWorld({ tx: 0, ty: 0, scale: 2 }, 200, 400)).toEqual({
      x: 100,
      y: 200,
    });
  });
});
