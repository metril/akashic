/**
 * Library-metadata facet panel on the Search page.
 *
 * Renders a strip of {field → top values (count)} chips populated from
 * the Meilisearch facet distribution returned by GET /api/search. Each
 * value is a click-to-filter button: clicking adds (or removes if
 * already active) the matching domain_metadata predicate via the
 * shared filter URL state.
 *
 * The panel only renders when at least one well-known field has a
 * non-empty distribution — for filesystem-only result sets the strip
 * is silent. Values are clipped to MAX_PER_FIELD; the user can refine
 * the search to surface lower-frequency values.
 */
import { useMemo } from "react";
import {
  DOMAIN_METADATA_FIELDS,
  type DomainMetadataField,
  type Predicate,
} from "../../lib/filterGrammar";
import { useFilterUrlState } from "../../hooks/useFilterUrlState";

const FIELD_LABELS: Record<DomainMetadataField, string> = {
  correspondent: "Correspondent",
  document_type: "Document type",
  person: "Person",
  album: "Album",
  camera_make: "Camera make",
  camera_model: "Camera model",
};

const MAX_PER_FIELD = 8;

interface Props {
  facetDistribution: Record<string, Record<string, number>> | null | undefined;
  className?: string;
}

export function DomainMetadataFacets({ facetDistribution, className }: Props) {
  const { filters, addFilter, removeFilter } = useFilterUrlState();

  // Pre-compute which (field, value) pairs are already on the URL so
  // chip rendering knows which to flag as "active". useMemo keeps the
  // Set stable across render — addFilter/removeFilter mutate the URL,
  // not this Set directly.
  const activePairs = useMemo(() => {
    const s = new Set<string>();
    for (const p of filters) {
      if (p.kind === "domain_metadata") s.add(`${p.field}:${p.value}`);
    }
    return s;
  }, [filters]);

  if (!facetDistribution) return null;

  const sections = DOMAIN_METADATA_FIELDS
    .map((field) => {
      const dist = facetDistribution[`domain_metadata.${field}`];
      if (!dist) return null;
      const values = Object.entries(dist)
        .filter(([, count]) => count > 0)
        .sort((a, b) => b[1] - a[1])
        .slice(0, MAX_PER_FIELD);
      if (values.length === 0) return null;
      return { field, values };
    })
    .filter((s): s is { field: DomainMetadataField; values: [string, number][] } => s !== null);

  if (sections.length === 0) return null;

  return (
    <div
      className={`rounded-md border border-line-subtle bg-surface-muted/40 px-3 py-2 ${className ?? ""}`}
    >
      <div className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle mb-2">
        Library metadata
      </div>
      <div className="space-y-1.5">
        {sections.map(({ field, values }) => (
          <div key={field} className="flex flex-wrap items-baseline gap-1.5">
            <span className="text-xs text-fg-muted w-32 flex-shrink-0">
              {FIELD_LABELS[field]}
            </span>
            <div className="flex flex-wrap gap-1.5">
              {values.map(([value, count]) => {
                const active = activePairs.has(`${field}:${value}`);
                const predicate: Predicate = { kind: "domain_metadata", field, value };
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
                        ? `Remove filter ${FIELD_LABELS[field]}: ${value}`
                        : `Filter to ${FIELD_LABELS[field]}: ${value}`
                    }
                  >
                    <span>{value}</span>
                    <span className="text-[10px] text-fg-subtle tabular-nums">
                      {count}
                    </span>
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
