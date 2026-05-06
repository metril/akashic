import { useCallback, useEffect, useRef, useState } from "react";

/**
 * v0.21.0 — touched-aware field-error tracker for the source / host
 * forms.
 *
 * The validator runs cheaply on every render (already does, in
 * AddSourceForm); this hook *gates* error rendering on whether the
 * user has touched the field. A blur-then-empty pattern surfaces a
 * red border + error message; a typed value clears it 300ms after the
 * user stops typing (debounce keeps mid-type characters from flashing
 * red prematurely).
 *
 * Usage:
 *
 *   const { touched, errors, markTouched } = useFieldValidation(
 *     validateFn(value),  // returns Record<field, message>
 *   );
 *
 *   <Input onBlur={() => markTouched("bucket")} error={errors.bucket} />
 *
 * Field names are arbitrary strings; the hook itself is generic. The
 * returned `errors` object only includes fields the user has touched
 * AND that the validator currently flags.
 */
export function useFieldValidation(
  liveErrors: Record<string, string>,
  debounceMs = 300,
): {
  errors: Record<string, string>;
  markTouched: (field: string) => void;
  resetTouched: () => void;
} {
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const [debounced, setDebounced] = useState<Record<string, string>>(liveErrors);
  const timer = useRef<number | undefined>(undefined);

  // Debounce the live errors so mid-keystroke red-border flashes don't
  // happen. We re-evaluate visibility from `touched` on every render.
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setDebounced(liveErrors);
    }, debounceMs);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [liveErrors, debounceMs]);

  const markTouched = useCallback((field: string) => {
    setTouched((prev) => {
      if (prev.has(field)) return prev;
      const next = new Set(prev);
      next.add(field);
      return next;
    });
  }, []);

  const resetTouched = useCallback(() => setTouched(new Set()), []);

  // Visible errors = intersect debounced errors with touched fields.
  const errors: Record<string, string> = {};
  for (const f of touched) {
    if (debounced[f]) errors[f] = debounced[f];
  }

  return { errors, markTouched, resetTouched };
}
