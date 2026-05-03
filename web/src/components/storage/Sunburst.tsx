/**
 * Canvas2D-rendered DaisyDisk-style sunburst (v0.4.14). Replaces the
 * SVG-per-arc renderer that was here pre-v0.4.14, which re-rendered
 * every arc on every hover-state change and pinned each arc with a
 * `transition: opacity 120ms` style — the same family of GPU
 * compositor cost the v0.4.13 Drawer fix targeted.
 *
 * Public component API unchanged: same TreemapProps subset, same
 * onLeafClick / onDirClick / onContextMenu / onHoverChange semantics.
 * StorageExplorer.tsx renders this exactly as before.
 *
 * Hot-path design (mirrors the v0.4.11 Treemap WebGL rewrite):
 *   - Layout + arc colorization done once per (root, mode, radius)
 *     in `buildArcLayout` (sunburstLayout.ts).
 *   - Hover lives in a ref, not React state; mouse-move triggers
 *     a single canvas redraw + an imperative tooltip transform
 *     update + lifting the chain to the page sidebar. No React
 *     reconciliation on cursor motion.
 *   - Hit-test via `hitTestArc` polar scan — linear over arc list,
 *     plenty fast at MAX_RINGS=6.
 *
 * Phase 4 wheel-as-drill is also wired here: scroll up = drill into
 * the hovered directory; scroll down = drill up.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";

import type { ColorMode } from "../../pages/StorageExplorer.types";
import { drawSunburst, sizeCanvas } from "./sunburstDraw";
import { buildArcLayout, type ArcSpec } from "./sunburstLayout";
import { hitTestArc } from "./sunburstHitTest";
import type { TreeNode } from "./Treemap";

interface SunburstProps {
  root: TreeNode;
  width: number;
  height: number;
  mode: ColorMode;
  onLeafClick?: (node: TreeNode) => void;
  onDirClick?: (node: TreeNode) => void;
  onContextMenu?: (node: TreeNode, x: number, y: number) => void;
  onHoverChange?: (chain: TreeNode[] | null) => void;
  /** v0.4.14 Phase 4 — wheel-out commits a drill UP to the parent
   *  view. Page wires this to its existing `goUp()` so the navigation
   *  semantics match the Treemap. */
  onGoUp?: () => void;
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

export function Sunburst({
  root,
  width,
  height,
  mode,
  onLeafClick,
  onDirClick,
  onContextMenu,
  onHoverChange,
  onGoUp,
}: SunburstProps) {
  const radius = Math.min(width, height) / 2;
  const cx = width / 2;
  const cy = height / 2;

  const arcs = useMemo(
    () => (radius > 0 ? buildArcLayout(root, mode, radius) : []),
    [root, mode, radius],
  );

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const tooltipNameRef = useRef<HTMLSpanElement>(null);
  const tooltipPathRef = useRef<HTMLSpanElement>(null);
  const tooltipSizeRef = useRef<HTMLSpanElement>(null);

  const hoverRef = useRef<ArcSpec | null>(null);
  const drillCooldownRef = useRef(false);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    drawSunburst({
      canvas,
      arcs,
      hoverKey: hoverRef.current?.key ?? null,
      cx,
      cy,
      radius,
    });
  }, [arcs, cx, cy, radius]);

  // Resize / arc-change → re-size canvas backing buffer + repaint.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    sizeCanvas(canvas, width, height);
    redraw();
  }, [width, height, redraw]);

  // Drop hover when the root changes (user navigated away). The
  // cached ArcSpec belongs to the old layout and is now stale; the
  // next mouse-move re-establishes hover against the new arcs.
  useEffect(() => {
    if (hoverRef.current) {
      hoverRef.current = null;
      onHoverChange?.(null);
    }
    if (tooltipRef.current) tooltipRef.current.style.opacity = "0";
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root]);

  function setHover(next: ArcSpec | null) {
    if (hoverRef.current?.key === next?.key) return;
    hoverRef.current = next;
    redraw();

    const tooltip = tooltipRef.current;
    if (tooltip) {
      tooltip.style.opacity = next ? "1" : "0";
      if (next) {
        if (tooltipNameRef.current) tooltipNameRef.current.textContent = next.data.name;
        if (tooltipPathRef.current) tooltipPathRef.current.textContent = next.data.path;
        if (tooltipSizeRef.current) tooltipSizeRef.current.textContent = formatBytes(next.data.size_bytes);
      }
    }
    onHoverChange?.(next ? next.chain : null);
  }

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;

      // Imperative tooltip position update — never triggers a
      // React render. Same pattern as the Treemap.
      const tooltip = tooltipRef.current;
      if (tooltip) {
        const tw = tooltip.offsetWidth || 280;
        const th = tooltip.offsetHeight || 56;
        const left = Math.min(Math.max(x + 12, 4), width - tw - 4);
        const top = Math.min(Math.max(y + 12, 4), height - th - 4);
        tooltip.style.transform = `translate3d(${left}px, ${top}px, 0)`;
      }

      const hit = hitTestArc(arcs, cx, cy, x, y);
      setHover(hit);
    },
    // setHover is recreated every render but stable in behaviour;
    // listing arcs/cx/cy/width/height covers what the closure reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [arcs, cx, cy, width, height],
  );

  const handleMouseLeave = useCallback(() => {
    setHover(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const hit = hoverRef.current;
      if (!hit) return;
      const data = hit.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      e.stopPropagation();
      const isDir = data.kind === "directory";
      if (isDir) onDirClick?.(data);
      else onLeafClick?.(data);
    },
    [onDirClick, onLeafClick],
  );

  const handleContextMenu = useCallback(
    (e: React.MouseEvent) => {
      const hit = hoverRef.current;
      if (!hit) return;
      const data = hit.data;
      if (data.kind === "other" || data.kind === "hidden") return;
      e.preventDefault();
      e.stopPropagation();
      const container = containerRef.current;
      const rect = container?.getBoundingClientRect();
      const px = rect ? e.clientX - rect.left : e.clientX;
      const py = rect ? e.clientY - rect.top : e.clientY;
      onContextMenu?.(data, px, py);
    },
    [onContextMenu],
  );

  // v0.4.14 Phase 4 — wheel-as-drill. Wheel up over a directory =
  // drill in. Wheel down = drill up. 350ms cooldown so a single
  // physical scroll motion only fires once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    function onWheelNative(e: WheelEvent) {
      if (drillCooldownRef.current) return;
      e.preventDefault();
      const wheelIn = e.deltaY < 0;
      if (wheelIn) {
        const data = hoverRef.current?.data;
        if (data && (data.kind === "directory" || data.kind === "hidden")) {
          drillCooldownRef.current = true;
          setTimeout(() => { drillCooldownRef.current = false; }, 350);
          onDirClick?.(data);
        }
      } else {
        if (!onGoUp) return;
        drillCooldownRef.current = true;
        setTimeout(() => { drillCooldownRef.current = false; }, 350);
        onGoUp();
      }
    }
    container.addEventListener("wheel", onWheelNative, { passive: false });
    return () => container.removeEventListener("wheel", onWheelNative);
  }, [onDirClick, onGoUp]);

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full select-none"
      style={{ width, height }}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
      onContextMenu={handleContextMenu}
    >
      <canvas
        ref={canvasRef}
        className="block"
        style={{ width, height, display: "block", cursor: "pointer" }}
      />
      {/* Tooltip — always mounted; opacity + content updated
          imperatively so cursor motion never triggers a React render. */}
      <div
        ref={tooltipRef}
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: 280,
          opacity: 0,
          pointerEvents: "none",
          transition: "opacity 80ms linear",
        }}
        className="rounded-md bg-fg/90 dark:bg-surface text-bg dark:text-fg text-xs px-2.5 py-1.5 shadow-lg border border-line"
      >
        <div className="font-medium truncate">
          <span ref={tooltipNameRef} />
        </div>
        <div className="font-mono text-[10px] opacity-80 truncate">
          <span ref={tooltipPathRef} />
        </div>
        <div className="tabular-nums opacity-90 mt-0.5">
          <span ref={tooltipSizeRef} />
        </div>
      </div>
    </div>
  );
}
