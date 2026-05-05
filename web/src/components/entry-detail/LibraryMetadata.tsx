/**
 * Library Metadata strip in the EntryDetail drawer.
 *
 * Tier 3 self-hosted libraries (Paperless-ngx, Immich) emit
 * provider-specific metadata into entries.domain_metadata. The
 * well-known keys (correspondent, document_type, person, album,
 * camera_make, camera_model) render as FilterableCells so a click
 * jumps to /search filtered to that key/value. Connector-emitted keys
 * outside the known list still render here, but as plain text — they
 * aren't filterable until they're added to DOMAIN_METADATA_FIELDS in
 * filterGrammar.ts and DOMAIN_METADATA_FACET_KEYS in services/search.py.
 */
import type { DomainMetadata } from "../../types";
import {
  DOMAIN_METADATA_FIELDS,
  type DomainMetadataField,
} from "../../lib/filterGrammar";
import { FilterableCell } from "../ui/FilterableCell";

const FIELD_LABELS: Record<DomainMetadataField, string> = {
  correspondent: "Correspondent",
  document_type: "Document type",
  tags: "Tags",
  person: "Person",
  album: "Album",
  camera_make: "Camera make",
  camera_model: "Camera model",
};

const KNOWN_FIELD_SET: ReadonlySet<string> = new Set(DOMAIN_METADATA_FIELDS);

function renderValue(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  return JSON.stringify(v);
}

function isStringArray(v: unknown): v is string[] {
  return Array.isArray(v) && v.every((x) => typeof x === "string");
}

interface Props {
  metadata: DomainMetadata;
}

export function LibraryMetadata({ metadata }: Props) {
  // Render known keys first, in the canonical order, so a row from
  // Paperless and a row from Immich don't reorder by hash. Then any
  // remaining keys, alphabetised.
  type KnownRow = { field: DomainMetadataField; value: unknown };
  const knownRows: KnownRow[] = [];
  for (const field of DOMAIN_METADATA_FIELDS) {
    const value = metadata[field];
    if (value != null) knownRows.push({ field, value });
  }

  const otherKeys = Object.keys(metadata)
    .filter((k) => !KNOWN_FIELD_SET.has(k))
    .sort();

  if (knownRows.length === 0 && otherKeys.length === 0) {
    return <p className="text-sm text-fg-subtle italic">None</p>;
  }

  return (
    <dl className="space-y-1.5">
      {knownRows.map(({ field, value }) => {
        // Multi-valued keys (paperless tags, immich faces) render as a
        // wrap of clickable chips. Each chip filters Search to that
        // single value via the existing crossPage predicate plumbing.
        if (isStringArray(value)) {
          return (
            <div key={field} className="flex items-baseline gap-3 text-sm">
              <dt className="w-32 flex-shrink-0 text-xs text-fg-muted">
                {FIELD_LABELS[field]}
              </dt>
              <dd className="min-w-0 flex-1 text-fg break-words flex flex-wrap gap-1">
                {value.map((v) => (
                  <FilterableCell
                    key={v}
                    predicate={{ kind: "domain_metadata", field, value: v }}
                    crossPage
                    className="px-1.5 py-0.5 text-xs rounded-full border border-line bg-surface-muted"
                  >
                    {v}
                  </FilterableCell>
                ))}
              </dd>
            </div>
          );
        }
        const text = renderValue(value);
        return (
          <div key={field} className="flex items-baseline gap-3 text-sm">
            <dt className="w-32 flex-shrink-0 text-xs text-fg-muted">
              {FIELD_LABELS[field]}
            </dt>
            <dd className="min-w-0 flex-1 text-fg break-words">
              <FilterableCell
                predicate={{ kind: "domain_metadata", field, value: text }}
                crossPage
              >
                {text}
              </FilterableCell>
            </dd>
          </div>
        );
      })}
      {otherKeys.map((key) => (
        <div key={key} className="flex items-baseline gap-3 text-sm">
          <dt className="w-32 flex-shrink-0 text-xs text-fg-muted">
            {key.replace(/_/g, " ")}
          </dt>
          <dd className="min-w-0 flex-1 text-fg break-words">
            {renderValue(metadata[key])}
          </dd>
        </div>
      ))}
    </dl>
  );
}
