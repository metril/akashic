import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { Icon } from "./ui";
import { cn } from "./ui/cn";

// Initials displayed when the user has no avatar (we don't fetch any
// today). Single letter for a clean look; collapse to the first
// alphabetic letter so usernames like "01admin" still render legibly.
function initial(username: string | undefined | null): string {
  if (!username) return "?";
  const m = username.match(/[a-z]/i);
  return (m ? m[0] : username[0] || "?").toUpperCase();
}

export function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click / Esc. Keeping the listener attached only
  // while the menu is open avoids unnecessary global handlers.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "inline-flex items-center gap-2 h-9 pl-1 pr-2 rounded-md",
          "text-fg hover:bg-surface-muted transition-colors",
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1",
        )}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span
          className="h-7 w-7 rounded-full bg-accent-100 text-accent-700 text-xs font-semibold flex items-center justify-center"
          aria-hidden
        >
          {initial(user?.username)}
        </span>
        <span className="hidden sm:inline text-sm font-medium max-w-[120px] truncate">
          {user?.username ?? "…"}
        </span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1.5 w-56 rounded-lg border border-line bg-surface shadow-lg py-1 z-50"
        >
          <div className="px-3 py-2 border-b border-line-subtle">
            <div className="text-sm font-medium text-fg truncate">
              {user?.username ?? "Signed in"}
            </div>
            {user?.email && (
              <div className="text-xs text-fg-muted truncate">{user.email}</div>
            )}
            {user?.role && (
              <div className="text-[11px] uppercase tracking-wide text-fg-subtle mt-0.5">
                {user.role}
              </div>
            )}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              navigate("/settings");
            }}
            className="w-full text-left px-3 py-1.5 text-sm text-fg hover:bg-surface-muted flex items-center gap-2"
          >
            <Icon name="settings" className="h-4 w-4 text-fg-subtle" />
            Settings
          </button>
          <div className="my-1 border-t border-line-subtle" />
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="w-full text-left px-3 py-1.5 text-sm text-fg hover:bg-surface-muted flex items-center gap-2"
          >
            <Icon name="sign-out" className="h-4 w-4 text-fg-subtle" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
