import { describe, expect, it } from "vitest";

import type { RenderInstance } from "./treemapGL";
import {
  easeOutCubic,
  interpolatePairs,
  matchInstances,
  type InstancePair,
} from "./treemapAnim";

function inst(
  x: number,
  y: number,
  w: number,
  h: number,
  fillA = 1,
): RenderInstance {
  return {
    x,
    y,
    w,
    h,
    fill: [1, 0, 0, fillA],
    stroke: [0, 0, 0, fillA],
    strokeWidth: 1,
  };
}

describe("easeOutCubic", () => {
  it("0 maps to 0 and 1 maps to 1", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
  });

  it("monotonically non-decreasing", () => {
    let prev = -Infinity;
    for (let t = 0; t <= 1; t += 0.05) {
      const v = easeOutCubic(t);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });
});

describe("matchInstances", () => {
  it("pairs matched keys, marks entering and exiting", () => {
    const oldI = [inst(0, 0, 10, 10), inst(20, 20, 10, 10)];
    const oldK = ["a", "b"];
    const newI = [inst(0, 0, 20, 20), inst(40, 40, 10, 10)];
    const newK = ["a", "c"];

    const pairs = matchInstances(oldI, oldK, newI, newK);
    // Output order: matched + exiting follow oldK order, then new-only.
    expect(pairs.length).toBe(3);
    // a: matched
    expect(pairs[0].from).toEqual(oldI[0]);
    expect(pairs[0].to).toEqual(newI[0]);
    // b: exiting
    expect(pairs[1].from).toEqual(oldI[1]);
    expect(pairs[1].to).toBeNull();
    // c: entering
    expect(pairs[2].from).toBeNull();
    expect(pairs[2].to).toEqual(newI[1]);
  });

  it("handles disjoint sets", () => {
    const pairs = matchInstances(
      [inst(0, 0, 1, 1)],
      ["a"],
      [inst(0, 0, 1, 1)],
      ["b"],
    );
    // 1 exiting + 1 entering
    expect(pairs.length).toBe(2);
    expect(pairs[0].to).toBeNull();
    expect(pairs[1].from).toBeNull();
  });
});

describe("interpolatePairs", () => {
  const matchedPair: InstancePair = {
    from: inst(0, 0, 10, 10),
    to: inst(100, 200, 50, 50),
  };

  it("matched: t=0 returns from, t=1 returns to", () => {
    const at0 = interpolatePairs([matchedPair], 0)[0];
    expect(at0.x).toBe(0);
    expect(at0.w).toBe(10);

    const at1 = interpolatePairs([matchedPair], 1)[0];
    expect(at1.x).toBe(100);
    expect(at1.w).toBe(50);
  });

  it("matched: t=0.5 lerps halfway", () => {
    const mid = interpolatePairs([matchedPair], 0.5)[0];
    expect(mid.x).toBe(50);
    expect(mid.y).toBe(100);
    expect(mid.w).toBe(30);
    expect(mid.h).toBe(30);
  });

  it("entering: alpha ramps from 0 to 1", () => {
    const enter: InstancePair = { from: null, to: inst(0, 0, 10, 10, 0.8) };
    const at0 = interpolatePairs([enter], 0)[0];
    expect(at0.fill[3]).toBe(0);
    const at1 = interpolatePairs([enter], 1)[0];
    expect(at1.fill[3]).toBeCloseTo(0.8);
  });

  it("exiting: alpha ramps from 1 to 0", () => {
    const exit: InstancePair = { from: inst(0, 0, 10, 10, 0.8), to: null };
    const at0 = interpolatePairs([exit], 0)[0];
    expect(at0.fill[3]).toBeCloseTo(0.8);
    const at1 = interpolatePairs([exit], 1)[0];
    expect(at1.fill[3]).toBeCloseTo(0);
  });
});
