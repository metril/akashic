import type { NtACE } from "../types";

/**
 * dedupeAces collapses pixel-perfect duplicate NT ACEs into a single
 * group with a count. Two ACEs are considered the same when they
 * match across the four dimensions the user can see: ace_type
 * (allow/deny), principal SID, sorted flags, sorted mask.
 *
 * Why this exists: some SMB servers (NAS appliances that survived a
 * domain-join → migration → re-permission cycle is the common one)
 * accumulate duplicate ACEs in their stored security descriptors.
 * Windows ACL editors don't compact them, and the underlying SD bytes
 * are valid, so the scanner reads them faithfully and stores them in
 * entry.acl. Rendering each row separately produced the noise the
 * user reported on /browse?source=…&path=86%2FSeason+1.
 *
 * Sorted-key comparison (rather than direct array equality) so
 * ACEs that have the same flags/mask in different insertion orders —
 * a real-world quirk of some Windows tools — collapse correctly.
 *
 * Keying on `sid` rather than `name` so an unresolved SID and its
 * later-resolved namesake don't accidentally collapse — and the row
 * count stays stable across the resolver-hook round-trip.
 *
 * Iteration order of the result matches first-encounter order in
 * `aces` (Map preserves insertion order), so the dedup output
 * preserves the audit-relevant top-to-bottom evaluation order of the
 * source ACL.
 */
export function dedupeAces(aces: NtACE[]): { ace: NtACE; count: number }[] {
  const groups = new Map<string, { ace: NtACE; count: number }>();
  for (const ace of aces) {
    const key = JSON.stringify([
      ace.ace_type,
      ace.sid,
      [...ace.flags].sort(),
      [...ace.mask].sort(),
    ]);
    const existing = groups.get(key);
    if (existing) existing.count++;
    else groups.set(key, { ace, count: 1 });
  }
  return [...groups.values()];
}
