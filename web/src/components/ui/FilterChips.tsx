/**
 * Sticky chip bar that mirrors `?filters=` and lets the user remove
 * predicates one at a time (X button on each chip) or jump to Search
 * carrying the predicates over (the "Switch to Search ›" link).
 *
 * The "Switch to Search" affordance only renders on Browse — there it's
 * the way to escape Browse's single-source scope when the user wants
 * cross-source results. On Search itself the link doesn't make sense.
 */
import { Link } from "react-router-dom";

import type { Predicate } from "../../lib/filterGrammar";
import { serialize } from "../../lib/filterGrammar";
import { useFilterUrlState } from "../../hooks/useFilterUrlState";
import { predicateLabel } from "./FilterableCell";
import { cn } from "./cn";

interface FilterChipsProps {
  /** When true, render the "Switch to Search ›" link. */
  showSwitchToSearch?: boolean;
  className?: string;
}

export function FilterChips({ showSwitchToSearch = false, className }: FilterChipsProps) {
  const { filters, removeFilter, clearFilters } = useFilterUrlState();

  if (filters.length === 0) return null;

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 px-3 py-2 rounded-lg bg-accent-50/60 dark:bg-accent-500/10 border border-accent-200/40 dark:border-accent-500/20",
        className,
      )}
    >
      <span className="text-xs font-medium text-fg-muted mr-1">Filters:</span>
      {filters.map((p, i) => (
        <Chip key={chipKey(p, i)} predicate={p} onRemove={() => removeFilter(p)} />
      ))}
      <button
        type="button"
        onClick={clearFilters}
        className="text-xs text-fg-subtle hover:text-fg ml-1 underline-offset-2 hover:underline"
      >
        Clear all
      </button>
      {showSwitchToSearch && (
        <Link
          to={`/search?filters=${serialize(filters)}`}
          className="ml-auto text-xs font-medium text-accent-700 hover:text-accent-800 hover:underline"
          title="Apply these filters across all sources in Search"
        >
          Switch to Search ›
        </Link>
      )}
    </div>
  );
}

function Chip({ predicate, onRemove }: { predicate: Predicate; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface border border-line px-2.5 py-0.5 text-xs text-fg">
      {predicateLabel(predicate)}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${predicateLabel(predicate)} filter`}
        className="text-fg-subtle hover:text-fg leading-none"
      >
        ×
      </button>
    </span>
  );
}

function chipKey(p: Predicate, idx: number): string {
  // Predicates are not stable identities (they're plain objects) so we
  // need a structural key. Index alone breaks animation when the user
  // removes a chip from the middle, so combine kind+value+idx.
  const v = "value" in p ? String(p.value) : "";
  return `${p.kind}:${v}:${idx}`;
}
