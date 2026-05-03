import { describe, expect, it } from "vitest";

import type { TreeNode } from "./Treemap";
import { buildArcLayout } from "./sunburstLayout";
import { hitTestArc } from "./sunburstHitTest";

function leaf(name: string, size: number): TreeNode {
  return { kind: "file", name, path: `/${name}`, size_bytes: size, color_key: "type:txt" };
}

describe("hitTestArc", () => {
  // Two equal-sized leaves so each gets a half-circle (left + right
  // of centre, with 0° = "12 o'clock" / +y).
  const tree: TreeNode = {
    kind: "directory",
    name: "/",
    path: "/",
    size_bytes: 0,
    children: [leaf("a.txt", 100), leaf("b.txt", 100)],
  };
  const RADIUS = 100;
  const arcs = buildArcLayout(tree, "type", RADIUS);
  const cx = 200;
  const cy = 200;

  it("returns null when the cursor is in the centre disc (r=0)", () => {
    expect(hitTestArc(arcs, cx, cy, cx, cy)).toBeNull();
  });

  it("returns null when the cursor is past the outer ring", () => {
    expect(hitTestArc(arcs, cx, cy, cx + RADIUS + 5, cy)).toBeNull();
  });

  it("hits a depth-1 arc when the cursor sits inside one of the two halves", () => {
    // Right side, mid-radius — should hit a depth-1 leaf.
    const hit = hitTestArc(arcs, cx, cy, cx + RADIUS / 2, cy);
    expect(hit).not.toBeNull();
    expect(hit!.depth).toBe(1);
  });

  it("returns the deepest arc when arcs nest (deepest = most-specific)", () => {
    const nested: TreeNode = {
      kind: "directory",
      name: "/",
      path: "/",
      size_bytes: 0,
      children: [
        {
          kind: "directory",
          name: "src",
          path: "/src",
          size_bytes: 0,
          children: [leaf("main.ts", 100)],
        },
      ],
    };
    const arcsN = buildArcLayout(nested, "type", RADIUS);
    // Cursor near the outer edge — deepest arc covers it.
    const hit = hitTestArc(arcsN, cx, cy, cx, cy - (RADIUS - 5));
    expect(hit).not.toBeNull();
    expect(hit!.data.name).toBe("main.ts");
  });

  it("skips the synthetic depth-0 root", () => {
    // The root covers the entire angular range r=[0, radius/MAX_RINGS],
    // but we never want hover on it.
    const r = 5;  // inside the root's radial band
    const hit = hitTestArc(arcs, cx, cy, cx + r, cy);
    if (hit !== null) {
      expect(hit.depth).toBeGreaterThan(0);
    }
  });
});
