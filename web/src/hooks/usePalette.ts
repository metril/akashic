import { createContext, useContext } from "react";

interface PaletteCtx {
  open: boolean;
  setOpen: (b: boolean) => void;
}

// Default no-op context — only meaningful inside PaletteProvider, but
// returning a stub keeps callers safe in tests/storybooks rendered
// outside the provider.
export const PaletteContext = createContext<PaletteCtx>({
  open: false,
  setOpen: () => {},
});

export function usePalette() {
  return useContext(PaletteContext);
}
