import { describe, expect, it } from "vitest";
import type { HierarchyRectangularNode } from "d3-hierarchy";

import type { TreeNode } from "./Treemap";
import { buildHitIndex, hitTest } from "./treemapHitTest";

// Minimal mock — buildHitIndex only reads x0/x1/y0/y1/depth/data + uses
// .descendants(). We fabricate a tiny "hierarchy" by hand.
function mockNode(
  data: TreeNode,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  depth: number,
): HierarchyRectangularNode<TreeNode> {
  return {
    data,
    x0,
    y0,
    x1,
    y1,
    depth,
    descendants: () => [],
    // The other HierarchyRectangularNode fields aren't used by the
    // hit-test module, so type-cast away.
  } as unknown as HierarchyRectangularNode<TreeNode>;
}

function mockLayout(
  nodes: HierarchyRectangularNode<TreeNode>[],
): HierarchyRectangularNode<TreeNode> {
  // Return a fake root whose descendants() yields all nodes.
  return {
    descendants: () => nodes,
  } as unknown as HierarchyRectangularNode<TreeNode>;
}

const fileData: TreeNode = {
  kind: "file",
  name: "f",
  path: "/f",
  size_bytes: 100,
};
const dirData: TreeNode = {
  kind: "directory",
  name: "d",
  path: "/d",
  size_bytes: 100,
};

describe("buildHitIndex", () => {
  it("orders by depth descending", () => {
    const root = mockNode(dirData, 0, 0, 100, 100, 0);
    const child = mockNode(fileData, 10, 10, 50, 50, 2);
    const parent = mockNode(dirData, 5, 5, 80, 80, 1);
    const layout = mockLayout([root, parent, child]);

    const idx = buildHitIndex(layout);
    expect(idx.map((r) => r.depth)).toEqual([2, 1, 0]);
  });
});

describe("hitTest", () => {
  it("returns the deepest containing rect", () => {
    // root contains a smaller dir, which contains an even smaller leaf.
    const root = mockNode(dirData, 0, 0, 100, 100, 0);
    const dir = mockNode(dirData, 10, 10, 60, 60, 1);
    const leaf = mockNode(fileData, 20, 20, 40, 40, 2);
    const layout = mockLayout([root, dir, leaf]);
    const idx = buildHitIndex(layout);

    // Inside leaf — leaf wins.
    expect(hitTest(idx, 25, 25)?.depth).toBe(2);
    // Inside dir but outside leaf — dir wins.
    expect(hitTest(idx, 50, 50)?.depth).toBe(1);
    // Inside root but outside dir — root wins.
    expect(hitTest(idx, 80, 80)?.depth).toBe(0);
  });

  it("returns null when no rect contains the point", () => {
    const layout = mockLayout([mockNode(fileData, 0, 0, 10, 10, 0)]);
    const idx = buildHitIndex(layout);
    expect(hitTest(idx, 100, 100)).toBeNull();
  });
});
