import { useState } from "react";
import { Badge, Card, EmptyState, Page, Skeleton } from "../components/ui";
import { AddHostForm } from "../components/hosts/AddHostForm";
import { HostDetail } from "../components/hosts/HostDetail";
import { useHosts } from "../hooks/useHosts";
import { useAuth } from "../hooks/useAuth";

export default function Hosts() {
  const { isAdmin } = useAuth();
  const hostsQuery = useHosts();
  const [openHostId, setOpenHostId] = useState<string | null>(null);

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
          {hostsQuery.data?.map((h) => (
            <button
              key={h.id}
              type="button"
              onClick={() => setOpenHostId(h.id)}
              className="block w-full text-left"
            >
              <Card padding="md" className="cursor-pointer hover:border-blue-300">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
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
          ))}
        </div>

        <div className="lg:col-span-1">
          {isAdmin && <AddHostForm />}
        </div>
      </div>

      <HostDetail
        hostId={openHostId}
        open={openHostId !== null}
        onClose={() => setOpenHostId(null)}
      />
    </Page>
  );
}
