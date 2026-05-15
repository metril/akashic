/**
 * Admin-sidebar chip that surfaces /api/health/services status at a
 * glance. Green when all four services answer, amber when at least
 * one is degraded, red on a hard liveness failure. Clicks through to
 * the System Status page for detail.
 *
 * Hidden when the query fails to load (network blip / not yet
 * authenticated) so the sidebar doesn't render a spinner inline.
 */
import { Link } from "react-router-dom";

import { useAuth } from "../../hooks/useAuth";
import { useServicesHealth } from "../../hooks/useServicesHealth";
import { cn } from "../ui";

export function ServicesHealthBadge({ collapsed = false }: { collapsed?: boolean }) {
  const { isAdmin } = useAuth();
  const { data, isError } = useServicesHealth({ enabled: isAdmin });

  if (!isAdmin || isError || !data) return null;

  const services = [data.postgres, data.redis, data.meilisearch, data.tika];
  const down = services.filter((s) => !s.ok).length;
  const variant: "ok" | "degraded" | "down" =
    down === 0 ? "ok" : down === services.length ? "down" : "degraded";

  const dotClass = {
    ok: "bg-emerald-500",
    degraded: "bg-amber-500",
    down: "bg-rose-500",
  }[variant];

  const label = {
    ok: "Services OK",
    degraded: `${down} degraded`,
    down: "All down",
  }[variant];

  return (
    <Link
      to="/admin/system-status"
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-1.5 text-xs",
        "text-muted hover:text-default hover:bg-surface-hover",
        "transition-colors",
        collapsed && "justify-center",
      )}
      aria-label={`Service status: ${label}`}
      title={
        collapsed
          ? `Services: ${label}`
          : `Postgres ${data.postgres.ok ? "ok" : "down"} · ` +
            `Redis ${data.redis.ok ? "ok" : "down"} · ` +
            `Meili ${data.meilisearch.ok ? "ok" : "down"} · ` +
            `Tika ${data.tika.ok ? "ok" : "down"}`
      }
    >
      <span className={cn("inline-block h-2 w-2 rounded-full", dotClass)} />
      {!collapsed && <span>{label}</span>}
    </Link>
  );
}
