/**
 * Wraps any cell content as a clickable affordance that adds a Phase-6
 * grammar predicate to the URL. The wrapper is purely visual — no
 * border, no chrome — until the user hovers; then a subtle outline +
 * pointer cursor signals "click to filter to this".
 *
 * Two click behaviours:
 *   - left-click adds the predicate via useFilterUrlState (no-op when
 *     an equivalent predicate is already present, so clicking the same
 *     owner cell twice doesn't stack)
 *   - cmd/ctrl-click does the same but routes the navigation through
 *     react-router's `<Link>` to /search, with the predicate carried —
 *     the "filter cross-source" shortcut from Browse.
 *
 * Stop-propagation is on by default because most use sites are inside
 * Browse rows whose own row-click opens the entry detail drawer. The
 * cell's filter intent should NOT also open the drawer.
 */
import { Link } from "react-router-dom";

import type { Predicate } from "../../lib/filterGrammar";
import { serialize } from "../../lib/filterGrammar";
import { useFilterUrlState } from "../../hooks/useFilterUrlState";
import { cn } from "./cn";

interface FilterableCellProps {
  predicate: Predicate;
  /** When true, render as a router-link to /search?filters=… instead of
   *  adding to the current page. Use for cells in pages that don't have
   *  their own filter UI (the EntryDetail drawer, ACL principal cells). */
  crossPage?: boolean;
  /** Visual override — defaults to inline-block hover affordance. Pass
   *  "block" or extra Tailwind classes when the cell needs to fill its
   *  grid track (e.g., a Browse row's owner column). */
  className?: string;
  title?: string;
  children: React.ReactNode;
}

export function FilterableCell({
  predicate,
  crossPage = false,
  className,
  title,
  children,
}: FilterableCellProps) {
  const { addFilter } = useFilterUrlState();

  if (crossPage) {
    const href = `/search?filters=${serialize([predicate])}`;
    return (
      <Link
        to={href}
        title={title ?? `Filter Search to ${predicateLabel(predicate)}`}
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "inline-flex items-center gap-1 rounded px-1 -mx-1 hover:bg-accent-50 hover:text-accent-700 transition-colors",
          className,
        )}
      >
        {children}
      </Link>
    );
  }

  return (
    <button
      type="button"
      onClick={(e) => {
        // Cmd/Ctrl-click → cross-page link via window.location to keep
        // this primitive non-Link in the common case (Link forces an
        // anchor element, which clobbers cell layout in tables).
        if (e.metaKey || e.ctrlKey) {
          e.preventDefault();
          e.stopPropagation();
          window.location.href = `/search?filters=${serialize([predicate])}`;
          return;
        }
        e.stopPropagation();
        addFilter(predicate);
      }}
      title={title ?? `Filter to ${predicateLabel(predicate)} (⌘-click for Search)`}
      className={cn(
        "inline-flex items-center gap-1 text-left rounded px-1 -mx-1",
        "hover:bg-accent-50 hover:text-accent-700 dark:hover:bg-accent-500/10",
        "transition-colors cursor-pointer",
        className,
      )}
    >
      {children}
    </button>
  );
}

export function predicateLabel(p: Predicate): string {
  switch (p.kind) {
    case "extension": return `extension: ${p.value}`;
    case "source":    return `source: ${p.value}`;
    case "owner":     return `owner: ${p.value}`;
    case "principal": return `${p.right ?? "read"} by ${p.value}`;
    case "mime":      return `mime: ${p.value}`;
    case "size":      return `size ${p.op === "gte" ? "≥" : p.op === "lte" ? "≤" : "="} ${p.value}`;
    case "mtime":     return `modified ${p.op === "gte" ? "≥" : "≤"} ${p.value}`;
    case "path":      return `under ${p.value}`;
    case "tag":       return `tag: ${p.value}`;
  }
}
