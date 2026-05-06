/**
 * v0.20.0 — three "result-shape" facets surfaced on the Search page.
 *
 * Source / MIME / extension already lived in the API as filterable
 * Meilisearch attributes; this strip exposes their per-bucket counts as
 * click-to-filter chips alongside the existing DomainMetadataFacets
 * panel. Same chip pattern, same `useFilterUrlState` plumbing, so the
 * predicates serialize into the existing `?filters=` URL state and
 * stay compatible with FilterChips.
 *
 * The "Source" row hides itself when the user has already restricted
 * to a single source via the legacy dropdown — counts of one are
 * uninformative and the chip would just duplicate the dropdown.
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../../api/client";
import { useFilterUrlState } from "../../hooks/useFilterUrlState";
import type { Predicate } from "../../lib/filterGrammar";
import type { Source } from "../../types";

const MAX_PER_FIELD = 8;

interface Props {
  facetDistribution: Record<string, Record<string, number>> | null | undefined;
  /** When set, the Source row is suppressed — the legacy dropdown is
   *  already pinning results to one source and the row would only show
   *  a single chip. */
  hideSource?: boolean;
  className?: string;
}

export function ResultFacets({ facetDistribution, hideSource, className }: Props) {
  const { filters, addFilter, removeFilter } = useFilterUrlState();

  // Fetch the sources list so we can render names rather than UUIDs.
  // React-query dedupes against the same key in Search.tsx — no extra
  // network request.
  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
  });
  const sourceLabel = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sourcesQuery.data ?? []) m.set(s.id, s.name);
    return m;
  }, [sourcesQuery.data]);

  const activeKeys = useMemo(() => {
    const s = new Set<string>();
    for (const p of filters) {
      if (p.kind === "source") s.add(`source:${p.value}`);
      else if (p.kind === "mime") s.add(`mime:${p.value}`);
      else if (p.kind === "extension") s.add(`extension:${p.value}`);
    }
    return s;
  }, [filters]);

  if (!facetDistribution) return null;

  const sections: { kind: "source" | "mime" | "extension"; label: string; values: [string, number][] }[] = [];

  if (!hideSource) {
    const sourceDist = facetDistribution["source_id"];
    if (sourceDist) {
      const values = Object.entries(sourceDist)
        .filter(([, count]) => count > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, MAX_PER_FIELD);
      if (values.length > 0) sections.push({ kind: "source", label: "Source", values });
    }
  }

  const mimeDist = facetDistribution["mime_type"];
  if (mimeDist) {
    const values = Object.entries(mimeDist)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_PER_FIELD);
    if (values.length > 0) sections.push({ kind: "mime", label: "MIME type", values });
  }

  const extDist = facetDistribution["extension"];
  if (extDist) {
    const values = Object.entries(extDist)
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, MAX_PER_FIELD);
    if (values.length > 0) sections.push({ kind: "extension", label: "Extension", values });
  }

  if (sections.length === 0) return null;

  return (
    <div
      className={`rounded-md border border-line-subtle bg-surface-muted/40 px-3 py-2 ${className ?? ""}`}
    >
      <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle mb-2">
        Refine results
      </div>
      <div className="space-y-1.5">
        {sections.map(({ kind, label, values }) => (
          <div key={kind} className="flex flex-wrap items-baseline gap-1.5">
            <span className="text-xs text-fg-muted w-32 flex-shrink-0">{label}</span>
            <div className="flex flex-wrap gap-1.5">
              {values.map(([value, count]) => {
                const active = activeKeys.has(`${kind}:${value}`);
                const predicate: Predicate = { kind, value } as Predicate;
                const display =
                  kind === "source"
                    ? sourceLabel.get(value) ?? value.slice(0, 8)
                    : kind === "extension"
                      ? `.${value}`
                      : value;
                return (
                  <button
                    key={value}
                    type="button"
                    onClick={() => (active ? removeFilter(predicate) : addFilter(predicate))}
                    aria-pressed={active}
                    className={
                      active
                        ? "inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border border-accent-500 bg-accent-100 text-accent-800"
                        : "inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border border-line bg-surface text-fg hover:bg-surface-muted"
                    }
                    title={
                      active
                        ? `Remove filter ${label}: ${display}`
                        : `Filter to ${label}: ${display}`
                    }
                  >
                    <span>{display}</span>
                    <span className="text-[10px] text-fg-subtle tabular-nums">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
