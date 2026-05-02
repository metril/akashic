/**
 * One row in the pending-claims pane: pairing code (BIG, monospace),
 * hostname, requested pool, time left. Inline approve/deny actions
 * with light-touch confirmations.
 */
import { useEffect, useState } from "react";

import { Badge, Button, Input } from "../ui";
import {
  useDiscoveryRequests,
  type DiscoveryRequest,
} from "../../hooks/useDiscoveryRequests";

interface Props {
  request: DiscoveryRequest;
}

function formatRemaining(expiresAt: string, now: number): string {
  const ms = Math.max(0, new Date(expiresAt).getTime() - now);
  const mins = Math.floor(ms / 60_000);
  const secs = Math.floor((ms % 60_000) / 1000);
  if (mins > 0) return `${mins}m ${secs}s left`;
  return `${secs}s left`;
}

export function PendingClaimRow({ request }: Props) {
  const { approve, deny } = useDiscoveryRequests();
  const [mode, setMode] = useState<"idle" | "approving" | "denying">("idle");
  const [name, setName] = useState(
    `${request.hostname || "scanner"}-${request.pairing_code.slice(0, 4)}`,
  );
  const [pool, setPool] = useState(request.requested_pool ?? "default");
  const [denyReason, setDenyReason] = useState("");

  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  return (
    <li className="px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-3">
            <code className="font-mono text-base font-semibold tracking-wider text-fg">
              {request.pairing_code}
            </code>
            <Badge variant="neutral">
              {request.hostname || "unknown host"}
            </Badge>
            {request.requested_pool && (
              <Badge variant="neutral">pool: {request.requested_pool}</Badge>
            )}
          </div>
          <div className="mt-1 text-xs text-fg-muted">
            {request.agent_version && `v${request.agent_version} · `}
            {formatRemaining(request.expires_at, now)}
          </div>
          <div
            className="mt-1 text-[10px] text-fg-subtle font-mono truncate"
            title={request.key_fingerprint}
          >
            {request.key_fingerprint.slice(0, 16)}…
          </div>
        </div>
        {mode === "idle" && (
          <div className="flex flex-col items-end gap-1.5">
            <Button size="sm" onClick={() => setMode("approving")}>
              Approve
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setMode("denying")}
            >
              Deny
            </Button>
          </div>
        )}
      </div>

      {mode === "approving" && (
        <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Input
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Pool"
            value={pool}
            onChange={(e) => setPool(e.target.value)}
          />
          <div className="sm:col-span-2 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setMode("idle")}>
              Cancel
            </Button>
            <Button
              loading={approve.isPending}
              onClick={async () => {
                if (!name.trim()) return;
                await approve.mutateAsync({
                  id: request.id,
                  body: { name: name.trim(), pool: pool.trim() || "default" },
                });
                setMode("idle");
              }}
            >
              Approve
            </Button>
          </div>
          {approve.isError && (
            <p className="sm:col-span-2 text-xs text-rose-600">
              {approve.error instanceof Error
                ? approve.error.message
                : "Failed to approve"}
            </p>
          )}
        </div>
      )}

      {mode === "denying" && (
        <div className="mt-3 space-y-2">
          <Input
            label="Reason (optional)"
            value={denyReason}
            onChange={(e) => setDenyReason(e.target.value)}
            placeholder="e.g. unknown host"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setMode("idle")}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={deny.isPending}
              onClick={async () => {
                await deny.mutateAsync({
                  id: request.id,
                  body: denyReason.trim()
                    ? { reason: denyReason.trim() }
                    : {},
                });
                setMode("idle");
              }}
            >
              Deny
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
