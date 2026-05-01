/**
 * Right-docked hover summary panel for the Storage Explorer canvas.
 *
 * Reads the lifted hover state from the page (the Treemap and
 * Sunburst both feed `onHoverChange(chain)`) and renders a breadcrumb
 * trail from root to the hovered node, with the node's size and quick
 * actions ("Open in Browse", "Filter Search to here").
 *
 * Each ancestor breadcrumb is clickable — hitting one drills the
 * canvas (via the page-supplied `onPathClick`) to that depth, the
 * same way clicking a directory rectangle does. Lets the user "rewind"
 * a deep hover without leaving the page.
 */
import { Link } from "react-router-dom";

import { serialize as serializeFilters } from "../../lib/filterGrammar";
import type { Predicate } from "../../lib/filterGrammar";
import type { TreeNode } from "./Treemap";

interface Props {
  chain: TreeNode[] | null;
  sourceId: string;
  onPathClick?: (path: string) => void;
}

export function HoverSidebar({ chain, sourceId, onPathClick }: Props) {
  if (!chain || chain.length === 0) {
    return (
      <div className="text-xs text-fg-subtle italic px-3 py-2">
        Hover a tile to inspect.
      </div>
    );
  }
  const tail = chain[chain.length - 1];
  const targetForFilter =
    tail.kind === "directory"
      ? tail.path
      : tail.path.split("/").slice(0, -1).join("/") || "/";
  const browsePath =
    tail.kind === "directory"
      ? tail.path
      : tail.path.split("/").slice(0, -1).join("/") || "/";
  const pathPred: Predicate = { kind: "path", value: targetForFilter };

  return (
    <div className="text-xs text-fg flex flex-col gap-3 px-3 py-3">
      <ol className="space-y-1">
        {chain.map((n, i) => {
          const isLast = i === chain.length - 1;
          // Every non-leaf ancestor is a directory (or the synthetic
          // "/" root) — both navigable as a `?path=` target.
          const isClickable = !isLast;
          const indent = i === 0 ? 0 : 4 * i;
          return (
            <li
              key={`${n.path}:${i}`}
              style={{ paddingLeft: indent }}
              className={
                "truncate " +
                (isLast
                  ? "font-medium text-fg"
                  : "text-fg-muted")
              }
            >
              {isClickable && onPathClick ? (
                <button
                  type="button"
                  onClick={() => onPathClick(n.path)}
                  className="hover:text-accent-700 truncate"
                  title={n.path}
                >
                  {n.name === "/" ? "/" : n.name}
                </button>
              ) : (
                <span title={n.path}>
                  {n.name === "/" ? "/" : n.name}
                </span>
              )}
            </li>
          );
        })}
      </ol>

      <div className="text-fg-muted tabular-nums">
        {formatBytes(tail.size_bytes)}
        {tail.kind !== "directory" && tail.kind !== "hidden" && (
          <span className="ml-1.5 text-fg-subtle">· {tail.kind}</span>
        )}
      </div>

      {tail.kind !== "other" && tail.kind !== "hidden" && (
        <div className="flex flex-col gap-1">
          <Link
            to={`/browse?source=${sourceId}&path=${encodeURIComponent(browsePath)}`}
            className="text-accent-700 hover:underline"
          >
            Open in Browse
          </Link>
          <Link
            to={`/search?filters=${serializeFilters([pathPred])}`}
            className="text-accent-700 hover:underline"
          >
            Filter Search to here
          </Link>
        </div>
      )}
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
