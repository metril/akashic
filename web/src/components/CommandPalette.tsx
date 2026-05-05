import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { toast } from "sonner";
import { api } from "../api/client";
import type { SearchResult, Source } from "../types";
import { useTheme } from "../hooks/useTheme";
import { usePalette } from "../hooks/usePalette";
import { Icon, Scrim } from "./ui";
import { useEntryDetail } from "../hooks/useEntryDetail";
import { formatBytes } from "../lib/format";

const RECENT_KEY = "palette-recent";
const RECENT_MAX = 5;

interface RecentItem {
  id: string;       // unique key per command (route, "source-open:<id>", etc.)
  label: string;    // display text
  hint?: string;    // sub-line
  to?: string;      // route to navigate
  // v0.5.12 — file results open the entry detail drawer (mounted
  // globally in Layout) rather than navigating to /browse?path=
  // which would treat the file as a directory.
  entryId?: string;
  action?: string;  // for non-route actions ("toggle-theme", etc.)
}

function readRecent(): RecentItem[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as RecentItem[]) : [];
  } catch {
    return [];
  }
}

function pushRecent(item: RecentItem) {
  try {
    const list = readRecent().filter((r) => r.id !== item.id);
    list.unshift(item);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
  } catch {
    // Best-effort.
  }
}

interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export function CommandPalette() {
  const { open, setOpen } = usePalette();
  const navigate = useNavigate();
  const { setMode, resolved } = useTheme();
  const { openEntry } = useEntryDetail();
  const [query, setQuery] = useState("");
  const [recent] = useState<RecentItem[]>(() => readRecent());

  // Reset query each open so the palette is a clean slate every time.
  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  // Esc to close — sonner/cmdk also handles this internally but a
  // top-level guard avoids subtle ordering issues with other dialogs.
  // Cmd/Ctrl+Enter on a non-empty query jumps straight to the
  // /search page with the query attached — Linear/Raycast-pattern
  // escape from the preview palette into the full search experience.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        const q = (e.target as HTMLInputElement | null)?.value?.trim?.()
          ?? query.trim();
        if (q) {
          setOpen(false);
          navigate(`/search?q=${encodeURIComponent(q)}`);
          e.preventDefault();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, setOpen, query, navigate]);

  // Load sources for the "Sources" group. The same useSources cache is
  // already warmed by the sidebar/dashboard so this is usually instant.
  const sourcesQuery = useQuery<Source[]>({
    queryKey: ["sources"],
    queryFn: () => api.get<Source[]>("/sources"),
    enabled: open,
  });
  const sources = sourcesQuery.data ?? [];

  // Debounce file search by ~150 ms — cmdk fires on every keystroke
  // and we don't want a /search request per character. Setter is gated
  // on `query` so closed-palette doesn't keep re-running.
  const [debouncedQ, setDebouncedQ] = useState("");
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => setDebouncedQ(query.trim()), 150);
    return () => clearTimeout(id);
  }, [query, open]);

  const fileSearch = useQuery<SearchResponse>({
    queryKey: ["palette-search", debouncedQ],
    queryFn: () => {
      const p = new URLSearchParams();
      p.set("q", debouncedQ);
      p.set("limit", "8");
      return api.get<SearchResponse>(`/search?${p.toString()}`);
    },
    enabled: open && debouncedQ.length >= 2,
  });

  const fileResults = fileSearch.data?.results ?? [];

  const navItems = useMemo(
    () => [
      { id: "/dashboard",  label: "Dashboard",  iconName: "dashboard"  as const },
      { id: "/browse",     label: "Browse",     iconName: "folder"     as const },
      { id: "/search",     label: "Search",     iconName: "search"     as const },
      { id: "/duplicates", label: "Duplicates", iconName: "duplicates" as const },
      { id: "/analytics",  label: "Analytics",  iconName: "analytics"  as const },
      { id: "/sources",    label: "Sources",    iconName: "sources"    as const },
      { id: "/settings",   label: "Settings",   iconName: "settings"   as const },
    ],
    [],
  );

  function go(item: RecentItem) {
    pushRecent(item);
    setOpen(false);
    if (item.entryId) {
      // v0.5.12 — open the file detail drawer overlaid on the
      // current page. Same UX as clicking a search result on /search.
      openEntry(item.entryId);
      return;
    }
    if (item.to) {
      navigate(item.to);
      return;
    }
    if (item.action === "toggle-theme") {
      setMode(resolved === "dark" ? "light" : "dark");
      return;
    }
    if (item.action?.startsWith("scan:")) {
      const sourceId = item.action.slice("scan:".length);
      const p = api.post("/scans/trigger", {
        source_id: sourceId,
        scan_type: "incremental",
      });
      toast.promise(p, {
        loading: "Triggering scan…",
        success: "Scan started.",
        error: (e: unknown) =>
          `Couldn't start scan: ${e instanceof Error ? e.message : "unknown error"}`,
      });
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[14vh] px-4"
      role="dialog"
      aria-modal="true"
    >
      <Scrim onClick={() => setOpen(false)} />
      <Command
        label="Command palette"
        className="relative w-full max-w-xl rounded-xl bg-surface border border-line/70 shadow-2xl overflow-hidden"
      >
        <div className="flex items-center gap-2 px-3 border-b border-line-subtle">
          <Icon name="search" className="h-4 w-4 text-fg-subtle" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            autoFocus
            placeholder="Search files, jump to a page, run an action…"
            className="flex-1 h-12 bg-transparent text-sm text-fg placeholder:text-fg-subtle focus:outline-none"
          />
          <kbd className="text-[10px] font-mono text-fg-subtle bg-app border border-line rounded px-1.5 py-0.5">
            Esc
          </kbd>
        </div>

        <Command.List className="max-h-[60vh] overflow-y-auto py-1">
          <Command.Empty className="px-3 py-6 text-center text-sm text-fg-muted">
            No matches.
          </Command.Empty>

          {!query && recent.length > 0 && (
            <Command.Group heading="Recent" className="text-meta uppercase text-fg-subtle px-3 pt-2">
              {recent.map((r) => (
                <Command.Item
                  key={`recent:${r.id}`}
                  value={`recent ${r.label} ${r.hint ?? ""}`}
                  onSelect={() => go(r)}
                  className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
                >
                  <Icon path="M3 12a9 9 0 1018 0 9 9 0 00-18 0zm9-5v5l3 2" className="h-4 w-4 text-fg-subtle" />
                  <span className="truncate">{r.label}</span>
                  {r.hint && (
                    <span className="ml-auto text-xs text-fg-subtle truncate">
                      {r.hint}
                    </span>
                  )}
                </Command.Item>
              ))}
            </Command.Group>
          )}

          {/* Files group: always visible once the user types — with explicit
              states so it never silently disappears. cmdk filters Items by
              the query string, but plain divs (the state messages) render
              unconditionally. */}
          {query.length > 0 && (
            <Command.Group heading="Files" className="text-meta uppercase text-fg-subtle px-3 pt-2">
              {query.length < 2 && (
                <div className="px-3 py-2 text-xs text-fg-muted italic">
                  Type at least 2 characters to search files.
                </div>
              )}
              {query.length >= 2 && fileSearch.isFetching && fileResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-fg-muted">
                  Searching files…
                </div>
              )}
              {query.length >= 2 && fileSearch.isError && (
                <div className="px-3 py-2 text-xs text-rose-600 dark:text-rose-400">
                  <div className="font-medium">File search unavailable.</div>
                  <div className="text-[11px] text-fg-muted mt-0.5">
                    {fileSearch.error instanceof Error
                      ? fileSearch.error.message
                      : "Check the search service and try again."}
                  </div>
                  <button
                    type="button"
                    onClick={() => fileSearch.refetch()}
                    className="mt-1 text-[11px] font-medium text-accent-600 hover:text-accent-700 underline"
                  >
                    Retry
                  </button>
                </div>
              )}
              {fileResults.map((f) => (
                <Command.Item
                  key={`file:${f.id}`}
                  value={`file ${f.filename} ${f.path}`}
                  onSelect={() =>
                    go({
                      id: `file:${f.id}`,
                      label: f.filename,
                      hint: f.path,
                      entryId: f.id,
                    })
                  }
                  className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
                >
                  <Icon name="file" className="h-4 w-4 text-fg-subtle" />
                  <span className="truncate flex-1 min-w-0">{f.filename}</span>
                  <span className="text-xs text-fg-subtle tabular-nums flex-shrink-0">
                    {formatBytes(f.size_bytes)}
                  </span>
                </Command.Item>
              ))}
              {query.length >= 2 &&
                !fileSearch.isFetching &&
                !fileSearch.isError &&
                fileResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-fg-muted">
                  No files match "{query}". Run a scan first if no source has been indexed yet.
                </div>
              )}
              {query.length === 1 && (
                <div className="flex items-center gap-2 px-3 py-2 text-xs text-fg-subtle italic">
                  <Icon name="search" className="h-3.5 w-3.5" />
                  Type more to search files. Press ⌘↵ to open Search.
                </div>
              )}
              {query.length >= 2 && (fileResults.length > 0 || !fileSearch.isError) && (
                <Command.Item
                  key="file-search-all"
                  value={`view all results ${query}`}
                  onSelect={() =>
                    go({
                      id: "search-all",
                      label: fileSearch.data?.total
                        ? `Show all ${fileSearch.data.total.toLocaleString()} results in Search`
                        : `Show all results in Search`,
                      to: `/search?q=${encodeURIComponent(query)}`,
                    })
                  }
                  className="flex items-center gap-2 px-3 py-2 text-xs text-accent-600 dark:text-accent-500 font-medium rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
                >
                  <Icon name="search" className="h-3.5 w-3.5" />
                  {fileSearch.data?.total !== undefined && fileSearch.data.total > fileResults.length
                    ? `Show all ${fileSearch.data.total.toLocaleString()} results in Search →`
                    : "Show all results in Search →"}
                </Command.Item>
              )}
            </Command.Group>
          )}

          <Command.Group heading="Navigate" className="text-meta uppercase text-fg-subtle px-3 pt-2">
            {navItems.map((n) => (
              <Command.Item
                key={`nav:${n.id}`}
                value={`navigate ${n.label}`}
                onSelect={() => go({ id: n.id, label: n.label, to: n.id })}
                className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
              >
                <Icon name={n.iconName} className="h-4 w-4 text-fg-subtle" />
                <span>{n.label}</span>
              </Command.Item>
            ))}
          </Command.Group>

          {sources.length > 0 && (
            <Command.Group heading="Sources" className="text-meta uppercase text-fg-subtle px-3 pt-2">
              {sources.map((s) => (
                <Command.Item
                  key={`source:${s.id}`}
                  value={`source open ${s.name}`}
                  onSelect={() =>
                    go({
                      id: `source-open:${s.id}`,
                      label: `Open: ${s.name}`,
                      to: `/sources?open=${s.id}`,
                    })
                  }
                  className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
                >
                  <Icon name="sources" className="h-4 w-4 text-fg-subtle" />
                  <span className="truncate flex-1">Open: {s.name}</span>
                  <span className="text-xs text-fg-subtle uppercase tracking-wide">
                    {s.type}
                  </span>
                </Command.Item>
              ))}
              {sources.map((s) => (
                <Command.Item
                  key={`scan:${s.id}`}
                  value={`source scan ${s.name}`}
                  onSelect={() =>
                    go({
                      id: `source-scan:${s.id}`,
                      label: `Scan now: ${s.name}`,
                      action: `scan:${s.id}`,
                    })
                  }
                  className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
                >
                  <Icon name="search" className="h-4 w-4 text-fg-subtle" />
                  <span className="truncate flex-1">Scan now: {s.name}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}

          <Command.Group heading="Actions" className="text-meta uppercase text-fg-subtle px-3 pt-2">
            <Command.Item
              key="toggle-theme"
              value="action toggle theme"
              onSelect={() =>
                go({ id: "toggle-theme", label: "Toggle theme", action: "toggle-theme" })
              }
              className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
            >
              <Icon path="M12 3v18M3 12h18" className="h-4 w-4 text-fg-subtle" />
              Toggle theme
            </Command.Item>
            <Command.Item
              key="add-source"
              value="action add source"
              onSelect={() =>
                go({ id: "/sources", label: "Add a source", to: "/sources" })
              }
              className="flex items-center gap-2 px-3 py-2 text-sm text-fg rounded-md cursor-pointer aria-selected:bg-accent-50 aria-selected:text-accent-700"
            >
              <Icon name="sources" className="h-4 w-4 text-fg-subtle" />
              Add a source
            </Command.Item>
          </Command.Group>
        </Command.List>
        <div className="flex items-center justify-between gap-3 border-t border-line-subtle px-3 py-1.5 text-[11px] text-fg-subtle">
          <span>
            <kbd className="font-mono bg-app border border-line rounded px-1">↵</kbd> open
            <span className="mx-1.5">·</span>
            <kbd className="font-mono bg-app border border-line rounded px-1">⌘↵</kbd> see all in Search
          </span>
          <span>Fuzzy preview · Search page supports glob/regex</span>
        </div>
      </Command>
    </div>
  );
}
