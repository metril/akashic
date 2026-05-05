import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Badge, Card, EmptyState, Page, ReachabilityDot, Skeleton, type ReachabilityState } from "../components/ui";
import { formatRelative } from "../lib/format";
import type { Host } from "../types";
import { AddHostForm } from "../components/hosts/AddHostForm";
import { HostDetail } from "../components/hosts/HostDetail";
import { useHosts } from "../hooks/useHosts";
import { useAuth } from "../hooks/useAuth";

// Same staleness threshold the source badge uses (2× check interval).
const STALENESS_THRESHOLD_MS = 10 * 60 * 1000;

function deriveHostState(h: Host): { state: ReachabilityState; label: string } {
  const stale =
    h.last_reachability_check_at != null &&
    Date.now() - Date.parse(h.last_reachability_check_at) > STALENESS_THRESHOLD_MS;
  if (h.is_reachable === true) {
    return stale
      ? { state: "stale", label: "Stale" }
      : { state: "reachable", label: "Reachable" };
  }
  if (h.is_reachable === false) {
    return { state: "unreachable", label: "Unreachable" };
  }
  if (h.last_reachability_check_at && stale) {
    return { state: "stale_unchecked", label: "Stale" };
  }
  return { state: "unchecked", label: "Not yet checked" };
}

export default function Hosts() {
  const { isAdmin } = useAuth();
  const hostsQuery = useHosts();
  const [openHostId, setOpenHostId] = useState<string | null>(null);
  const [autoDiscover, setAutoDiscover] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();

  // Deep-link support: /hosts?host=<id>&discover=1 opens the drawer
  // and auto-expands the Discover panel. Used by AddSourceForm's
  // "Or discover all shares on this host" affordance to land the
  // user directly in the right flow.
  useEffect(() => {
    const id = searchParams.get("host");
    const discover = searchParams.get("discover") === "1";
    if (id) {
      setOpenHostId(id);
      setAutoDiscover(discover);
      // Clear the query string so a refresh doesn't re-trigger the
      // deep-link behaviour.
      const next = new URLSearchParams(searchParams);
      next.delete("host");
      next.delete("discover");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Page
      title="Hosts"
      description="Reusable connection targets — add many shares to one host without re-entering credentials."
      width="wide"
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-3">
          {hostsQuery.isLoading && <Skeleton className="h-32 w-full" />}
          {hostsQuery.isSuccess && hostsQuery.data.length === 0 && (
            <Card padding="md">
              <EmptyState
                title="No hosts yet"
                description={
                  isAdmin
                    ? "Add a host on the right, then attach shares from the Sources page."
                    : "Ask an administrator to add hosts."
                }
              />
            </Card>
          )}
          {hostsQuery.data?.map((h) => {
            const reach = deriveHostState(h);
            const tooltip = h.last_reachability_check_at
              ? `${reach.label} · last check ${formatRelative(h.last_reachability_check_at)}`
              : reach.label;
            return (
              <button
                key={h.id}
                type="button"
                onClick={() => setOpenHostId(h.id)}
                className="block w-full text-left"
              >
                <Card padding="md" className="cursor-pointer hover:border-accent-300 dark:hover:border-accent-400">
                  <div className="flex items-center justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span title={tooltip}>
                          <ReachabilityDot state={reach.state} />
                        </span>
                        <span className="font-semibold text-fg truncate">
                          {h.name}
                        </span>
                        <Badge variant="neutral">{h.type}</Badge>
                      </div>
                      <p className="text-xs text-fg-muted">
                        {h.source_count === 0
                          ? "No attached shares"
                          : `${h.source_count} attached share${h.source_count === 1 ? "" : "s"}`}
                      </p>
                    </div>
                  </div>
                </Card>
              </button>
            );
          })}
        </div>

        <div className="lg:col-span-1">
          {isAdmin && <AddHostForm />}
        </div>
      </div>

      <HostDetail
        hostId={openHostId}
        open={openHostId !== null}
        onClose={() => {
          setOpenHostId(null);
          setAutoDiscover(false);
        }}
        autoDiscover={autoDiscover}
      />
    </Page>
  );
}
