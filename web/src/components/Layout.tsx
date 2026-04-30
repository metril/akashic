import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import { CommandPalette } from "./CommandPalette";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { PaletteContext } from "../hooks/usePalette";

export default function Layout() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  useDocumentTitle();

  return (
    <PaletteContext.Provider value={{ open: paletteOpen, setOpen: setPaletteOpen }}>
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
      </div>
    </PaletteContext.Provider>
  );
}
