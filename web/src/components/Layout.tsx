import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import { CommandPalette } from "./CommandPalette";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { PaletteContext } from "../hooks/usePalette";
import { EntryDetailProvider } from "../hooks/useEntryDetail";
import { Drawer } from "./ui";
import { EntryDetail } from "./EntryDetail";

export default function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Lifted entry-detail drawer state — any page can call openEntry(id)
  // via useEntryDetail. Browse and Search both consume; the drawer
  // itself only renders here.
  const [openEntryId, setOpenEntryId] = useState<string | null>(null);
  useDocumentTitle();

  return (
    <PaletteContext.Provider value={{ open: paletteOpen, setOpen: setPaletteOpen }}>
      <EntryDetailProvider value={{ openEntry: setOpenEntryId, openEntryId }}>
        <div className="flex min-h-screen bg-app">
          <Sidebar
            mobileOpen={mobileNavOpen}
            onMobileClose={() => setMobileNavOpen(false)}
          />

          <div className="flex-1 flex flex-col min-w-0">
            <TopBar onMobileNavOpen={() => setMobileNavOpen(true)} />
            <main className="flex-1 overflow-auto">
              <Outlet />
            </main>
          </div>

          <KeyboardShortcuts />
          <CommandPalette />
          <Drawer
            open={!!openEntryId}
            onClose={() => setOpenEntryId(null)}
            title="Entry detail"
            width="lg"
          >
            <EntryDetail entryId={openEntryId} />
          </Drawer>
        </div>
      </EntryDetailProvider>
    </PaletteContext.Provider>
  );
}
