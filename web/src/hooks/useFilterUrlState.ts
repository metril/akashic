/**
 * Read/write the predicate list from `?filters=<base64url>` so it
 * survives reload, back-button, and copy-paste of the URL. Used by
 * Browse, Search, and AdminAccess once Phase 6 lands the chip UI.
 *
 * Contract:
 *   - `setFilters([])` removes the param entirely so empty state stays
 *     out of the URL bar.
 *   - Predicate order is preserved; the chip UI uses that order.
 *   - Other URL params (source_id, path, q, …) are left untouched.
 */
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import { Predicate, deserialize, sameTarget, serialize } from "../lib/filterGrammar";

const PARAM = "filters";

export function useFilterUrlState(): {
  filters: Predicate[];
  setFilters: (next: Predicate[]) => void;
  addFilter: (p: Predicate) => void;
  removeFilter: (p: Predicate) => void;
  clearFilters: () => void;
} {
  const [params, setParams] = useSearchParams();
  const filters = deserialize(params.get(PARAM));

  const writeNext = useCallback(
    (next: Predicate[]) => {
      const newParams = new URLSearchParams(params);
      if (next.length === 0) newParams.delete(PARAM);
      else newParams.set(PARAM, serialize(next));
      setParams(newParams, { replace: false });
    },
    [params, setParams],
  );

  const setFilters = useCallback(
    (next: Predicate[]) => writeNext(next),
    [writeNext],
  );

  const addFilter = useCallback(
    (p: Predicate) => {
      // No-op if an equivalent predicate is already on the URL — this is
      // the "click same cell twice doesn't stack" guarantee. The chip UI
      // wraps this with a real toggle when the same cell is the trigger
      // (call removeFilter on the second click).
      if (filters.some((existing) => sameTarget(existing, p))) return;
      writeNext([...filters, p]);
    },
    [filters, writeNext],
  );

  const removeFilter = useCallback(
    (p: Predicate) => {
      writeNext(filters.filter((existing) => !sameTarget(existing, p)));
    },
    [filters, writeNext],
  );

  const clearFilters = useCallback(() => writeNext([]), [writeNext]);

  return { filters, setFilters, addFilter, removeFilter, clearFilters };
}
