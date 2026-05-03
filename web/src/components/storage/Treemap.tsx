/**
 * WebGL2-rendered squarified treemap (v0.4.11). DaisyDisk / WinDirStat
 * shape: every leaf is a coloured rectangle inside its directory's
 * container, one canvas, one zoom level.
 *
 * v0.4.11 rewrite: replaces the SVG-per-node renderer (which emitted
 * 5000+ DOM nodes and re-rendered every node on every pixel of mouse
 * motion) with a single instanced WebGL draw call. Mouse-move triggers
 * no redraws — only hover-node IDENTITY changes do, and the tooltip
 * follows the cursor via imperative DOM mutation. Headroom for 50k+
 * rects at 60 fps.
 *
 * Public API unchanged from the SVG version: same Treemap props, same
 * onLeafClick / onDirClick / onContextMenu / onHoverChange callbacks,
 * same TreeNode type. StorageExplorer.tsx doesn't change.
 *
 * Visual treatment preserved:
 *   - Branch accent palette shared across descendants of a top-level dir
 *   - Depth-aware directory chrome (header band darkens; plate / border
 *     alpha steps with depth)
 *   - Hover lifts the stroke of every ancestor back to the root in the
 *     branch accent so the eye can trace the path
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  hierarchy as d3Hierarchy,
  treemap as d3Treemap,
  treemapSquarify,
  type HierarchyRectangularNode,
} from "d3-hierarchy";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { branchAccent, mix } from "./branchAccent";
import { createGLRenderer, parseColor, type GLRenderer, type RenderInstance, type Rgba } from "./treemapGL";
import { buildHitIndex, hitTest, type HitRect } from "./treemapHitTest";
import { interpolatePairs, matchInstances, runAnim } from "./treemapAnim";
import { clamp as clampViewport, IDENTITY as IDENTITY_VIEWPORT, SCALE_MAX, screenToWorld, zoomAt, type Viewport } from "./treemapViewport";

export interface TreeNode {
  id?: string;
  kind: "file" | "directory" | "other" | "hidden";
  name: string;
  path: string;
  size_bytes: number;
  color_key?: string;
  children?: TreeNode[];
}

interface TreemapProps {
  root: TreeNode;
  width: number;
  height: number;
  mode: ColorMode;
  onLeafClick?: (node: TreeNode) => void;
  onDirClick?: (node: TreeNode) => void;
  onContextMenu?: (node: TreeNode, x: number, y: number) => void;
  onHoverChange?: (chain: TreeNode[] | null) => void;
  /** v0.4.14 Phase 4 — wheel-out at IDENTITY viewport commits a
   *  drill UP to the parent. Page wires this to its existing
   *  `goUp()` so navigation semantics match the Sunburst. */
  onGoUp?: () => void;
}

const PALETTE = [
  "#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#0ea5e9",
];

function colorFor(key: string | undefined, mode: ColorMode): string {
  if (!key) return "#94a3b8";
  if (mode === "age") {
    if (key === "hot") return "#10b981";
    if (key === "warm") return "#f59e0b";
    if (key === "cold") return "#94a3b8";
    return "#cbd5e1";
  }
  if (mode === "risk") {
    if (key === "public") return "#ef4444";
    if (key === "authenticated") return "#f59e0b";
    if (key === "restricted") return "#10b981";
    return "#94a3b8";
  }
  if (key === "other") return "#94a3b8";
  if (key === "directory") return "#475569";
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return PALETTE[Math.abs(h) % PALETTE.length];
}

/** Headroom on each directory rectangle for its title strip. Recursive
 *  because the nested layout needs each level's headroom subtracted from
 *  its children's available height. */
function paddingTopFor(d: HierarchyRectangularNode<TreeNode>): number {
  if (d.depth === 0) return 0;
  const h = (d.y1 ?? 0) - (d.y0 ?? 0);
  return h >= 28 ? 14 : 0;
}

/** Depth-aware directory chrome. Same math as the SVG version. */
function dirStyle(depth: number, accent: string) {
  const d = Math.max(1, depth);
  const headerMix = Math.min(0.85, 0.10 + 0.10 * (d - 1));
  const plateAlpha = Math.min(0.10, 0.04 + 0.015 * (d - 1));
  const borderAlpha = Math.max(0.10, 0.32 - 0.05 * (d - 1));
  const strokeWidth = Math.max(0.5, 2.5 - 0.5 * (d - 1));
  return {
    headerFill: mix(accent, "#0f172a", headerMix),
    plateFill: `rgba(15, 23, 42, ${plateAlpha})`,
    borderFill: `rgba(15, 23, 42, ${borderAlpha})`,
    strokeWidth,
  };
}

function topLevelName(n: HierarchyRectangularNode<TreeNode>): string {
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur && cur.depth > 1 && cur.parent) cur = cur.parent;
  return cur?.data.name ?? "/";
}

const HEADER_H = 14;
const TRANSPARENT: Rgba = [0, 0, 0, 0];

interface LabelSpec {
  key: string;
  x: number;
  y: number;
  w: number;
  text: string;
  isHeader: boolean;
}

function truncate(s: string, max: number): string {
  if (max < 2) return "";
  if (s.length <= max) return s;
  return s.slice(0, Math.max(1, max - 1)) + "…";
}

interface BaseScene {
  instances: RenderInstance[];
  keys: string[];
  labels: LabelSpec[];
}

const EMPTY_SCENE: BaseScene = { instances: [], keys: [], labels: [] };

/** Build the layout-driven base scene (instances + keys + labels). Pure
 *  function of (layout, mode) — no hover state. Hover highlighting is a
 *  separate cheap overlay (see `buildHighlightOverlay`) so cursor moves
 *  don't trigger a full descendants iteration on every node crossing. */
function buildBaseScene(
  layout: HierarchyRectangularNode<TreeNode>,
  mode: ColorMode,
): BaseScene {
  const instances: RenderInstance[] = [];
  const keys: string[] = [];
  const labels: LabelSpec[] = [];

  for (const n of layout.descendants()) {
    const x0 = n.x0 ?? 0;
    const y0 = n.y0 ?? 0;
    const x1 = n.x1 ?? 0;
    const y1 = n.y1 ?? 0;
    const w = x1 - x0;
    const h = y1 - y0;
    if (w < 1 || h < 1) continue;

    const isRoot = n.depth === 0;
    const data = n.data;
    const isDir = data.kind === "directory" || data.kind === "hidden";
    const isLeaf = !isDir;
    const accent = branchAccent(topLevelName(n));
    const ds = isDir ? dirStyle(n.depth, accent) : null;

    const fillStr = isLeaf
      ? colorFor(data.color_key, mode)
      : ds!.plateFill;
    const baseStroke = isDir ? ds!.borderFill : "rgba(255,255,255,0.55)";
    const baseStrokeWidth = isDir ? ds!.strokeWidth : 0.5;

    instances.push({
      x: x0,
      y: y0,
      w,
      h,
      fill: parseColor(fillStr),
      stroke: parseColor(baseStroke),
      strokeWidth: baseStrokeWidth,
    });
    keys.push(`${data.path}:plate`);

    const showLabel = w >= 60 && h >= 16 && !isRoot;
    const showDirHeader = isDir && h >= 28 && w >= 60 && !isRoot;

    if (showDirHeader) {
      instances.push({
        x: x0,
        y: y0,
        w,
        h: HEADER_H,
        fill: parseColor(ds!.headerFill),
        stroke: TRANSPARENT,
        strokeWidth: 0,
      });
      keys.push(`${data.path}:header`);
      labels.push({
        key: `${data.path}:dh`,
        x: x0 + 4,
        y: y0,
        w,
        text: truncate(data.name, Math.floor(w / 6)),
        isHeader: true,
      });
    }
    if (showLabel && isLeaf) {
      labels.push({
        key: `${data.path}:lf`,
        x: x0 + 4,
        y: y0,
        w,
        text: truncate(data.name, Math.floor(w / 6)),
        isHeader: false,
      });
    }
  }

  return { instances, keys, labels };
}

/** Cheap hover-highlight overlay: one small instance per ancestor in
 *  the chain back to root. Drawn on top of the base scene to outline
 *  the path the cursor is following. Walks at most ~10 nodes; trivial
 *  to recompute on every hover change. */
function buildHighlightOverlay(
  hover: HierarchyRectangularNode<TreeNode> | null,
): RenderInstance[] {
  if (!hover) return EMPTY_OVERLAY;
  const overlay: RenderInstance[] = [];
  let cur: HierarchyRectangularNode<TreeNode> | null = hover;
  while (cur && cur.depth > 0) {
    const x0 = cur.x0 ?? 0;
    const y0 = cur.y0 ?? 0;
    const x1 = cur.x1 ?? 0;
    const y1 = cur.y1 ?? 0;
    const w = x1 - x0;
    const h = y1 - y0;
    if (w >= 1 && h >= 1) {
      const accent = branchAccent(topLevelName(cur));
      overlay.push({
        x: x0,
        y: y0,
        w,
        h,
        // Transparent fill — we want the base plate to show through;
        // this overlay is purely an accent stroke.
        fill: TRANSPARENT,
        stroke: parseColor(accent),
        strokeWidth: 1.75,
      });
    }
    cur = cur.parent ?? null;
  }
  return overlay;
}

const EMPTY_OVERLAY: RenderInstance[] = [];

function buildChain(n: HierarchyRectangularNode<TreeNode>): TreeNode[] {
  const chain: TreeNode[] = [];
  let cur: HierarchyRectangularNode<TreeNode> | null = n;
  while (cur) {
    chain.unshift(cur.data);
    cur = cur.parent ?? null;
  }
  return chain;
}

export function Treemap({
  root,
  width,
  height,
  mode,
  onLeafClick,
  onDirClick,
  onContextMenu,
  onHoverChange,
  onGoUp,
}: TreemapProps) {
  const layout = useMemo(() => {
    if (width <= 0 || height <= 0) return null;
    const h = d3Hierarchy<TreeNode>(root, (d) => d.children)
      .sum((d) => (d.children && d.children.length > 0 ? 0 : d.size_bytes))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
    d3Treemap<TreeNode>()
      .tile(treemapSquarify)
      .size([width, height])
      .paddingInner(1)
      .paddingTop(paddingTopFor)
      .round(true)(h);
    return h as HierarchyRectangularNode<TreeNode>;
  }, [root, width, height]);

  const hitIndex: HitRect[] = useMemo(
    () => (layout ? buildHitIndex(layout) : []),
    [layout],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const glRef = useRef<GLRenderer | null>(null);
  const [glFailed, setGlFailed] = useState(false);
  const [hoverNode, setHoverNode] = useState<HierarchyRectangularNode<TreeNode> | null>(
    null,
  );

  // Init / dispose the WebGL renderer once per canvas mount.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = createGLRenderer(canvas);
    if (!renderer) {
      setGlFailed(true);
      return;
    }
    glRef.current = renderer;
    return () => {
      renderer.dispose();
      glRef.current = null;
    };
  }, []);

  // v0.4.14 Phase 1 — base scene depends only on (layout, mode). Hover
  // is overlaid as a tiny separate array (ancestor chain outlines).
  // Cursor moves never trigger a full descendants iteration anymore;
  // the label `<span>` tree also stays stable across hover changes.
  const baseScene = useMemo(
    () => (layout ? buildBaseScene(layout, mode) : EMPTY_SCENE),
    [layout, mode],
  );
  const overlay = useMemo(
    () => buildHighlightOverlay(hoverNode),
    [hoverNode],
  );
  const drawnInstances = useMemo(
    () => (overlay.length ? [...baseScene.instances, ...overlay] : baseScene.instances),
    [baseScene, overlay],
  );

  // v0.4.11 Phase 9 — viewport state for free pan + wheel-zoom.
  // IDENTITY = no transform. Reset on root change (Phase 9f's "simple"
  // option — clean transition, doesn't try to compose with the drill
  // animation's layout interpolation).
  const [viewport, setViewport] = useState<Viewport>(IDENTITY_VIEWPORT);
  // Drag state lives in a ref to avoid per-frame re-renders.
  const draggingRef = useRef<{ x: number; y: number } | null>(null);

  // v0.4.11 Phase 7 — animated drill. When the `root` prop changes
  // (user clicked a directory or breadcrumb), interpolate from the
  // previously-displayed base scene to the new one over 300ms via the
  // WebGL renderer. Mode/hover changes don't animate — they just
  // redraw immediately. Resize doesn't animate either — same.
  //
  // v0.4.14 Phase 1 — animation matches the BASE scene only (layout
  // + mode). The hover overlay is appended on every static frame and
  // suppressed during animations (the morphing rects would just
  // produce visual noise under a stable highlight chain anyway).
  const prevRootRef = useRef(root);
  const prevBaseSceneRef = useRef(baseScene);
  const animCancelRef = useRef<(() => void) | null>(null);
  const [animating, setAnimating] = useState(false);

  useEffect(() => {
    if (!glRef.current) return;
    glRef.current.resize(width, height);

    const rootChanged = prevRootRef.current !== root;
    if (!rootChanged) {
      glRef.current.draw(drawnInstances, viewport);
      prevBaseSceneRef.current = baseScene;
      return;
    }

    prevRootRef.current = root;
    animCancelRef.current?.();

    const fromInstances = prevBaseSceneRef.current.instances;
    const fromKeys = prevBaseSceneRef.current.keys;
    const toInstances = baseScene.instances;
    const toKeys = baseScene.keys;

    if (fromInstances.length === 0) {
      glRef.current.draw(drawnInstances, viewport);
      prevBaseSceneRef.current = baseScene;
      return;
    }

    setAnimating(true);
    const pairs = matchInstances(fromInstances, fromKeys, toInstances, toKeys);
    animCancelRef.current = runAnim({
      durationMs: 300,
      onFrame: (t) => {
        glRef.current?.draw(interpolatePairs(pairs, t), viewport);
      },
      onDone: () => {
        glRef.current?.draw(drawnInstances, viewport);
        prevBaseSceneRef.current = baseScene;
        setAnimating(false);
        animCancelRef.current = null;
      },
    });
    // baseScene + drawnInstances are derived; deps reflect both so
    // hover overlay changes still re-trigger a draw on the
    // no-root-change path.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawnInstances, baseScene, root, width, height, viewport]);

  // Cancel any in-flight animation on unmount so the rAF loop doesn't
  // keep firing against a disposed renderer.
  useEffect(() => {
    return () => {
      animCancelRef.current?.();
      animCancelRef.current = null;
    };
  }, []);

  // Root change → hovered node belongs to the OLD layout's node tree
  // and is now stale. Drop it so the hit-test starts fresh on the
  // new layout. Mouse-move from the user re-establishes hover. Also
  // reset the viewport (Phase 9f simple option) so navigation always
  // starts at fit-to-container.
  useEffect(() => {
    setHoverNode(null);
    onHoverChange?.(null);
    setViewport(IDENTITY_VIEWPORT);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root]);

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // v0.4.11 Phase 9 — pan via shift+drag. Updates viewport state
      // imperatively (well, through React state, but only while the
      // user is dragging — short bursts).
      if (draggingRef.current) {
        const dx = x - draggingRef.current.x;
        const dy = y - draggingRef.current.y;
        draggingRef.current = { x, y };
        setViewport((v) =>
          clampViewport({ ...v, tx: v.tx + dx, ty: v.ty + dy }, width, height),
        );
        return;
      }

      // Imperative tooltip position update — does NOT trigger a React
      // render, so mouse-move stays free of reconciliation cost.
      const tooltip = tooltipRef.current;
      if (tooltip) {
        const tw = tooltip.offsetWidth || 280;
        const th = tooltip.offsetHeight || 56;
        const left = Math.min(Math.max(x + 12, 4), width - tw - 4);
        const top = Math.min(Math.max(y + 12, 4), height - th - 4);
        tooltip.style.transform = `translate3d(${left}px, ${top}px, 0)`;
      }

      // Hit-test in world coords (apply inverse viewport transform).
      const w = screenToWorld(viewport, x, y);
      const hit = hitTest(hitIndex, w.x, w.y);
      const next = hit?.node ?? null;
      if (next !== hoverNode) {
        setHoverNode(next);
        onHoverChange?.(next ? buildChain(next) : null);
      }
    },
    [hitIndex, hoverNode, onHoverChange, width, height, viewport],
  );

  // v0.4.11 Phase 9 — wheel-zoom (scroll up = zoom in around cursor).
  // React 17+ attaches onWheel as a passive listener (preventDefault is
  // a no-op there), so we wire up a non-passive native listener via
  // addEventListener so the page doesn't scroll when the user zooms
  // the treemap.
  //
  // v0.4.14 Phase 4 — at the zoom extremes the wheel commits a drill:
  //   - wheel-in past SCALE_MAX with a directory under the cursor
  //     fires onDirClick on it (rebases the root).
  //   - wheel-out at IDENTITY viewport fires onGoUp.
  // 350ms cooldown so a single physical scroll motion only fires
  // one drill — otherwise a fast wheel could cascade through several
  // levels in one gesture.
  const viewportRef = useRef(viewport);
  useEffect(() => { viewportRef.current = viewport; }, [viewport]);
  const hitIndexRef = useRef(hitIndex);
  useEffect(() => { hitIndexRef.current = hitIndex; }, [hitIndex]);
  const drillCooldownRef = useRef(false);
  // Refs for the drill callbacks so the wheel listener (mounted once
  // per width/height) can call the latest callback without being
  // recreated as props churn.
  const onDirClickRef = useRef(onDirClick);
  useEffect(() => { onDirClickRef.current = onDirClick; }, [onDirClick]);
  const onGoUpRef = useRef(onGoUp);
  useEffect(() => { onGoUpRef.current = onGoUp; }, [onGoUp]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    function onWheelNative(e: WheelEvent) {
      const c = containerRef.current;
      if (!c) return;
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const factor = e.deltaY > 0 ? 1 / 1.1 : 1.1;
      const v = viewportRef.current;

      // Wheel-in past SCALE_MAX → drill into the directory under cursor.
      if (factor > 1 && v.scale >= SCALE_MAX - 0.001 && !drillCooldownRef.current) {
        const w = screenToWorld(v, cx, cy);
        const hit = hitIndexRef.current.length
          ? hitTest(hitIndexRef.current, w.x, w.y)
          : null;
        if (hit && hit.node.depth > 0 && hit.node.data.kind === "directory") {
          drillCooldownRef.current = true;
          setTimeout(() => { drillCooldownRef.current = false; }, 350);
          onDirClickRef.current?.(hit.node.data);
          return;
        }
      }

      // Wheel-out at IDENTITY → drill up to parent.
      if (
        factor < 1
        && v.scale <= 1.001
        && Math.abs(v.tx) < 0.5 && Math.abs(v.ty) < 0.5
        && !drillCooldownRef.current
      ) {
        const goUp = onGoUpRef.current;
        if (goUp) {
          drillCooldownRef.current = true;
          setTimeout(() => { drillCooldownRef.current = false; }, 350);
          goUp();
          return;
        }
      }

      // Otherwise: normal zoom around cursor.
      setViewport((curr) => clampViewport(zoomAt(curr, cx, cy, factor), width, height));
    }
    container.addEventListener("wheel", onWheelNative, { passive: false });
    return () => container.removeEventListener("wheel", onWheelNative);
  }, [width, height]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    // Middle-click OR shift+left-click starts a pan drag. Plain
    // left-click stays for navigation; right-click for context menu.
    if (e.button !== 1 && !(e.button === 0 && e.shiftKey)) return;
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    draggingRef.current = { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, []);

  const handleMouseUp = useCallback(() => {
    draggingRef.current = null;
  }, []);

  // [Fit] reset — restore identity viewport.
  const handleFit = useCallback(() => {
    setViewport(IDENTITY_VIEWPORT);
  }, []);

  const handleMouseLeave = useCallback(() => {
    if (hoverNode !== null) {
      setHoverNode(null);
      onHoverChange?.(null);
    }
  }, [hoverNode, onHoverChange]);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      // Phase 9 — shift+click is the pan-drag modifier; suppress
      // navigation when shift was held even if the drag never moved.
      if (e.shiftKey) return;
      if (!hoverNode) return;
      const data = hoverNode.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      if (hoverNode.depth === 0) return;
      e.stopPropagation();
      if (data.kind === "directory") onDirClick?.(data);
      else onLeafClick?.(data);
    },
    [hoverNode, onDirClick, onLeafClick],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      if (!hoverNode) return;
      const data = hoverNode.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      if (hoverNode.depth === 0) return;
      e.preventDefault();
      e.stopPropagation();
      const container = containerRef.current;
      const rect = container?.getBoundingClientRect();
      const px = rect ? e.clientX - rect.left : e.clientX;
      const py = rect ? e.clientY - rect.top : e.clientY;
      onContextMenu?.(data, px, py);
    },
    [hoverNode, onContextMenu],
  );

  if (glFailed) {
    return (
      <div
        ref={containerRef}
        className="w-full h-full flex items-center justify-center text-fg-subtle text-sm p-4 text-center"
      >
        Treemap unavailable — your browser doesn&apos;t support WebGL2.
        Try the Sunburst view or open the Browse page.
      </div>
    );
  }

  // Cursor hint depends on what the user is doing. Dragging = grabbing;
  // hovering an interactive node = pointer; otherwise default.
  const cursorStyle = draggingRef.current
    ? "grabbing"
    : hoverNode && hoverNode.depth > 0
      ? "pointer"
      : "default";

  const isZoomed =
    viewport.scale !== 1 || viewport.tx !== 0 || viewport.ty !== 0;

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full select-none"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onMouseDown={handleMouseDown}
      onMouseUp={handleMouseUp}
      onClick={handleClick}
      onContextMenu={handleContextMenu}
      style={{ cursor: cursorStyle }}
    >
      <canvas
        ref={canvasRef}
        className="block"
        style={{ width, height, display: "block" }}
      />
      {/* Phase 9 — [Fit] reset button. Only visible when the user has
          panned/zoomed away from identity. Floating top-right; clicks
          stop-propagated so they don't pass through to the canvas. */}
      {isZoomed && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            handleFit();
          }}
          className="absolute top-2 right-2 z-10 rounded-md border border-line bg-surface/95 px-2.5 py-1 text-xs font-medium text-fg shadow hover:bg-surface"
        >
          Fit
        </button>
      )}
      {/* Label overlay — pointer-events-none so the canvas still
          receives all mouse events. Hidden during the drill animation
          (rects are still morphing, so labels at the final position
          would float oddly above mid-transit canvas content). v0.4.11
          Phase 9: outer div carries the viewport transform so labels
          track the zoomed/panned canvas content. transform-origin
          top-left matches the shader's coordinate convention. */}
      <div
        className="absolute inset-0 pointer-events-none overflow-hidden"
        style={{
          width,
          height,
          opacity: animating ? 0 : 1,
          transition: animating ? "none" : "opacity 120ms linear",
          transform: `translate(${viewport.tx}px, ${viewport.ty}px) scale(${viewport.scale})`,
          transformOrigin: "top left",
        }}
      >
        {baseScene.labels.map((l) => (
          <span
            key={l.key}
            style={{
              position: "absolute",
              left: l.x,
              top: l.y + (l.isHeader ? 1 : 3),
              maxWidth: l.w - 4,
              color: "#ffffff",
              fontSize: 10,
              fontWeight: l.isHeader ? 600 : 500,
              fontFamily:
                "ui-sans-serif, system-ui, -apple-system, sans-serif",
              textShadow: l.isHeader ? "none" : "0 1px 2px rgba(0,0,0,0.45)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              lineHeight: "12px",
            }}
          >
            {l.text}
          </span>
        ))}
      </div>
      {/* Tooltip — always mounted; opacity toggles. Position is
          updated imperatively on mouse-move, so React never sees a
          tooltip render during cursor motion. Content re-renders only
          when hoverNode changes. */}
      <div
        ref={tooltipRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 280,
          opacity: hoverNode && !animating ? 1 : 0,
          pointerEvents: "none",
          transition: "opacity 80ms linear",
        }}
        className="rounded-md bg-fg/90 dark:bg-surface text-bg dark:text-fg text-xs px-2.5 py-1.5 shadow-lg border border-line"
      >
        {hoverNode && (
          <>
            <div className="font-medium truncate">{hoverNode.data.name}</div>
            <div className="font-mono text-[10px] opacity-80 truncate">
              {hoverNode.data.path}
            </div>
            <div className="tabular-nums opacity-90 mt-0.5">
              {formatBytes(hoverNode.data.size_bytes)}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}
