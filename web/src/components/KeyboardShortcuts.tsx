import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { usePalette } from "../hooks/usePalette";

// Mac uses ⌘, Windows/Linux uses Ctrl. Detected once on mount.
const IS_MAC =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/i.test(navigator.userAgent);

const SHORTCUTS: { keys: string; description: string }[] = [
  { keys: IS_MAC ? "⌘ K" : "Ctrl K", description: "Open quick search" },
  { keys: "/", description: "Focus search" },
  { keys: "g d", description: "Go to Dashboard" },
  { keys: "g b", description: "Go to Browse" },
  { keys: "g s", description: "Go to Search" },
  { keys: "g r", description: "Go to Sources" },
  { keys: "g p", description: "Go to Duplicates" },
  { keys: "g a", description: "Go to Analytics" },
  { keys: "g t", description: "Go to Settings" },
  { keys: "?", description: "Show this help" },
  { keys: "Esc", description: "Close dialog / drawer" },
];

// Skip when typing into an input/textarea/contenteditable. Without this
// guard, "g d" while filling the source name would steal the keystrokes.
function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  return false;
}

export function KeyboardShortcuts() {
  const navigate = useNavigate();
  const palette = usePalette();
  const [helpOpen, setHelpOpen] = useState(false);
  // `g` is a chord prefix — pressing g alone arms the next key for ~1.5s.
  // Without this state, "g d" would require both keys to be held
  // simultaneously, which is awkward.
  const [pendingG, setPendingG] = useState(false);

  useEffect(() => {
    function handler(e: KeyboardEvent) {
      // Cmd+K / Ctrl+K — open command palette. Always handle, even from
      // inputs, because the user typing "Ctrl+K" while in a field
      // clearly wants the palette.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        palette.setOpen(true);
        return;
      }

      if (isTypingTarget(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === "?") {
        e.preventDefault();
        setHelpOpen(true);
        return;
      }
      if (e.key === "Escape") {
        setHelpOpen(false);
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        palette.setOpen(true);
        return;
      }

      if (e.key === "g" && !pendingG) {
        setPendingG(true);
        // 1.5 s window to type the second key — long enough for a slow
        // user, short enough that an idle "g" doesn't haunt the next
        // unrelated keystroke.
        setTimeout(() => setPendingG(false), 1500);
        return;
      }

      if (pendingG) {
        const route: Record<string, string> = {
          d: "/dashboard",
          b: "/browse",
          s: "/search",
          r: "/sources",
          p: "/duplicates",
          a: "/analytics",
          t: "/settings",
        };
        const target = route[e.key.toLowerCase()];
        setPendingG(false);
        if (target) {
          e.preventDefault();
          navigate(target);
        }
      }
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [navigate, pendingG, palette]);

  if (!helpOpen) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="shortcuts-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
    >
      <div
        className="absolute inset-0 bg-gray-900/40 backdrop-blur-[2px]"
        onClick={() => setHelpOpen(false)}
      />
      <div className="relative w-full max-w-md rounded-xl bg-surface border border-line/70 shadow-2xl p-5">
        <h2 id="shortcuts-title" className="text-base font-semibold text-fg mb-3">
          Keyboard shortcuts
        </h2>
        <dl className="space-y-1.5 text-sm">
          {SHORTCUTS.map((s) => (
            <div key={s.keys} className="flex items-center justify-between gap-4">
              <dt className="text-fg">{s.description}</dt>
              <dd>
                <kbd className="font-mono text-xs bg-surface-muted text-fg px-1.5 py-0.5 rounded border border-line">
                  {s.keys}
                </kbd>
              </dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 flex justify-end">
          <button
            type="button"
            onClick={() => setHelpOpen(false)}
            className="text-sm text-fg-muted hover:text-fg"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
