/**
 * Lifted entry-detail drawer state. Phase 6 hoists the drawer from
 * individual pages (Browse had its own) into Layout so any page can
 * call `openEntry(id)` without owning the Drawer component itself.
 *
 * Why a context-backed provider instead of a global Zustand-style
 * singleton: the rest of the app uses react-query + react-context
 * patterns; introducing a third state library for one drawer adds
 * surface area without paying for itself.
 */
import { createContext, useContext } from "react";

interface EntryDetailContext {
  openEntry: (entryId: string | null) => void;
  openEntryId: string | null;
}

const Ctx = createContext<EntryDetailContext | null>(null);

export const EntryDetailProvider = Ctx.Provider;

export function useEntryDetail(): EntryDetailContext {
  const ctx = useContext(Ctx);
  if (!ctx) {
    // Pages that aren't inside Layout (e.g. Login) must not call this.
    // Throw so the bug is loud rather than silently swallowing the call.
    throw new Error("useEntryDetail must be used within Layout's provider");
  }
  return ctx;
}
