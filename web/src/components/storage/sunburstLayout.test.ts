import { describe, expect, it } from "vitest";

import type { TreeNode } from "./Treemap";
import { buildArcLayout, MAX_RINGS, truncateDepth } from "./sunburstLayout";

function leaf(name: string, size: number, key = "type:txt"): TreeNode {
  return { kind: "file", name, path: `/${name}`, size_bytes: size, color_key: key };
}

describe("truncateDepth", () => {
  it("returns the input unchanged when shallower than the cap", () => {
    const t: TreeNode = {
      kind: "directory",
      name: "/",
      path: "/",
      size_bytes: 0,
      children: [leaf("a.txt", 100), leaf("b.txt", 200)],
    };
    expect(truncateDepth(t, MAX_RINGS)).toEqual(t);
  });

  it("rolls deep subtrees into a synthetic '…' leaf at the cap", () => {
    // Build a chain deeper than MAX_RINGS so the rollup must trigger.
    let node: TreeNode = leaf("deep.txt", 42);
    for (let i = 0; i < MAX_RINGS + 2; i++) {
      node = {
        kind: "directory",
        name: `d${i}`,
        path: `/${i}`,
        size_bytes: 0,
        children: [node],
      };
    }
    const out = truncateDepth(node, MAX_RINGS);

    // Walk down and assert that at depth MAX_RINGS - 1 we hit a `…` leaf.
    let cur: TreeNode | undefined = out;
    let depth = 0;
    while (cur?.children && cur.children.length > 0 && depth < MAX_RINGS + 5) {
      cur = cur.children[0];
      depth++;
    }
    expect(cur?.name).toBe("…");
    expect(cur?.size_bytes).toBe(42);
  });
});

describe("buildArcLayout", () => {
  it("returns at least the root + one ring of children", () => {
    const tree: TreeNode = {
      kind: "directory",
      name: "/",
      path: "/",
      size_bytes: 0,
      children: [leaf("a.txt", 100), leaf("b.txt", 200)],
    };
    const arcs = buildArcLayout(tree, "type", 200);
    expect(arcs.length).toBeGreaterThanOrEqual(3);
    expect(arcs[0].depth).toBe(0);  // root first
    const ringOne = arcs.filter((a) => a.depth === 1);
    expect(ringOne.length).toBe(2);
  });

  it("emits angles in [0, 2π] and the depth-1 sweep covers a full circle", () => {
    const tree: TreeNode = {
      kind: "directory",
      name: "/",
      path: "/",
      size_bytes: 0,
      children: [leaf("a.txt", 100), leaf("b.txt", 100)],
    };
    const arcs = buildArcLayout(tree, "type", 200);
    const ringOne = arcs.filter((a) => a.depth === 1);
    const sweep = ringOne.reduce((s, a) => s + (a.x1 - a.x0), 0);
    expect(sweep).toBeCloseTo(2 * Math.PI, 5);
    for (const a of arcs) {
      expect(a.x0).toBeGreaterThanOrEqual(0);
      expect(a.x1).toBeLessThanOrEqual(2 * Math.PI + 1e-6);
    }
  });

  it("populates ancestorPaths + chain root-first to leaf", () => {
    const tree: TreeNode = {
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
    const arcs = buildArcLayout(tree, "type", 200);
    const leafArc = arcs.find((a) => a.data.name === "main.ts");
    expect(leafArc).toBeDefined();
    expect(leafArc!.ancestorPaths).toContain("/");
    expect(leafArc!.ancestorPaths).toContain("/src");
    expect(leafArc!.chain[0].name).toBe("/");
    expect(leafArc!.chain[leafArc!.chain.length - 1].name).toBe("main.ts");
  });

  it("returns an empty list for a non-positive radius", () => {
    const tree: TreeNode = { kind: "directory", name: "/", path: "/", size_bytes: 0 };
    expect(buildArcLayout(tree, "type", 0)).toEqual([]);
    expect(buildArcLayout(tree, "type", -10)).toEqual([]);
  });
});
