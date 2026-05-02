/**
 * Three sections, one route:
 *   1. Active scanners — registered agents, with scope summary cols
 *   2. Join tokens — admin mints one-time tokens scanners self-claim with
 *   3. Pending claims — discovery requests waiting for an admin decision
 *
 * The legacy "create scanner with manual key" flow lives under an
 * Advanced disclosure for break-glass use; the recommended path is
 * the join-token wizard (Section 2) since the private key never
 * leaves the scanner host.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  Input,
  Page,
  Spinner,
} from "../components/ui";
import { JoinTokenWizard } from "../components/scanners/JoinTokenWizard";
import { PendingClaimRow } from "../components/scanners/PendingClaimRow";
import { DiscoveryToggle } from "../components/scanners/DiscoveryToggle";
import { useScannerClaimTokens } from "../hooks/useScannerClaimTokens";
import { useDiscoveryRequests } from "../hooks/useDiscoveryRequests";
import { useServerSetting } from "../hooks/useServerSetting";
import { useSources } from "../hooks/useSources";

interface Scanner {
  id: string;
  name: string;
  pool: string;
  key_fingerprint: string;
  hostname: string | null;
  version: string | null;
  protocol_version: number | null;
  registered_at: string;
  last_seen_at: string | null;
  enabled: boolean;
  online: boolean;
  allowed_source_ids: string[] | null;
  allowed_scan_types: string[] | null;
}

interface ScannerCreated {
  id: string;
  name: string;
  pool: string;
  public_key_pem: string;
  private_key_pem: string;
  key_fingerprint: string;
  protocol_version: number;
}

function formatRelative(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.round(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.round(ms / 3_600_000)}h ago`;
  return `${Math.round(ms / 86_400_000)}d ago`;
}

export default function SettingsScanners() {
  const qc = useQueryClient();
  const scannersQ = useQuery<Scanner[]>({
    queryKey: ["scanners"],
    queryFn: () => api.get<Scanner[]>("/scanners"),
    refetchInterval: 15_000,
  });

  const rotateMut = useMutation<ScannerCreated, Error, string>({
    mutationFn: (id) => api.post<ScannerCreated>(`/scanners/${id}/rotate`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
  });

  const patchMut = useMutation<Scanner, Error, { id: string; enabled: boolean }>({
    mutationFn: ({ id, enabled }) => api.patch<Scanner>(`/scanners/${id}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
  });

  const deleteMut = useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/scanners/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
  });

  const [issued, setIssued] = useState<ScannerCreated | null>(null);
  const [rotateConfirm, setRotateConfirm] = useState<Scanner | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  async function handleRotate(scanner: Scanner) {
    const result = await rotateMut.mutateAsync(scanner.id);
    setIssued(result);
    setRotateConfirm(null);
  }

  function handleDelete(scanner: Scanner) {
    if (
      confirm(
        `Delete scanner "${scanner.name}"? Any in-flight scan it was holding will be re-queued. Its private key stops working immediately.`,
      )
    ) {
      deleteMut.mutate(scanner.id);
    }
  }

  return (
    <Page
      title="Scanners"
      description="Registered agents and the tokens / pending claims that bring new ones online."
      width="default"
    >
      <div className="space-y-6">
        {/* ── Active scanners ─────────────────────────────────────── */}
        <section>
          <CardHeader title="Active scanners" />
          {scannersQ.isLoading ? (
            <div className="flex items-center justify-center py-12 text-fg-subtle">
              <Spinner />
            </div>
          ) : scannersQ.isError ? (
            <div className="text-sm text-rose-600 bg-rose-50 rounded px-3 py-2">
              {scannersQ.error instanceof Error
                ? scannersQ.error.message
                : "Failed to load scanners"}
            </div>
          ) : (scannersQ.data ?? []).length === 0 ? (
            <Card padding="lg">
              <EmptyState
                title="No scanners registered yet"
                description="Generate a join token below, then run akashic-scanner claim on a host that can reach your sources."
              />
            </Card>
          ) : (
            <Card padding="none">
              <ul className="divide-y divide-line-subtle">
                {(scannersQ.data ?? []).map((s) => (
                  <ScannerRow
                    key={s.id}
                    scanner={s}
                    onRotate={() => setRotateConfirm(s)}
                    onToggle={() =>
                      patchMut.mutate({ id: s.id, enabled: !s.enabled })
                    }
                    onDelete={() => handleDelete(s)}
                    deleteLoading={
                      deleteMut.isPending && deleteMut.variables === s.id
                    }
                  />
                ))}
              </ul>
            </Card>
          )}
        </section>

        {/* ── Join tokens ─────────────────────────────────────────── */}
        <section id="tokens">
          <div className="flex items-center justify-between mb-2">
            <CardHeader title="Join tokens" />
            <Button onClick={() => setWizardOpen(true)}>+ Generate token</Button>
          </div>
          <p className="text-xs text-fg-muted mb-3">
            Recommended path. Generate a one-time token, paste it into
            the scanner's run command — the scanner generates its own
            keypair locally and self-registers. The private key never
            leaves the scanner host.
          </p>
          <JoinTokensList />
        </section>

        {/* ── Pending claims ──────────────────────────────────────── */}
        <section id="pending">
          <PendingClaimsSection />
        </section>

        {/* ── Advanced (manual key) ───────────────────────────────── */}
        <section>
          <details className="border border-line rounded p-4">
            <summary className="cursor-pointer text-sm font-medium text-fg">
              Advanced — register with a server-generated key (legacy)
            </summary>
            <p className="text-xs text-fg-muted mt-2 mb-3">
              The api generates the keypair and returns the private
              key once. Useful for scripted automation that already
              depends on this flow; for new scanners prefer a join
              token (above).
            </p>
            <ManualKeyForm onIssued={setIssued} />
          </details>
        </section>
      </div>

      {wizardOpen && <JoinTokenWizard onClose={() => setWizardOpen(false)} />}
      {issued && (
        <KeyIssuedModal data={issued} onClose={() => setIssued(null)} />
      )}
      {rotateConfirm && (
        <RotateConfirm
          scanner={rotateConfirm}
          onCancel={() => setRotateConfirm(null)}
          onConfirm={() => handleRotate(rotateConfirm)}
          pending={rotateMut.isPending}
        />
      )}
    </Page>
  );
}

function ScannerRow({
  scanner: s, onRotate, onToggle, onDelete, deleteLoading,
}: {
  scanner: Scanner;
  onRotate: () => void;
  onToggle: () => void;
  onDelete: () => void;
  deleteLoading: boolean;
}) {
  const sourcesQ = useSources();
  const sourceNames = (sourcesQ.data ?? []).reduce<Record<string, string>>(
    (acc, src) => {
      acc[src.id] = src.name;
      return acc;
    }, {},
  );
  const sourceScope = s.allowed_source_ids;
  const typeScope = s.allowed_scan_types;
  return (
    <li className="px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`size-2 rounded-full ${
                s.online ? "bg-emerald-500" : "bg-fg-subtle"
              }`}
              aria-label={s.online ? "online" : "offline"}
            />
            <span className="font-medium text-fg truncate">{s.name}</span>
            <Badge variant="neutral">{s.pool}</Badge>
            {!s.enabled && <Badge variant="neutral">disabled</Badge>}
            {sourceScope && sourceScope.length > 0 && (
              <span
                title={sourceScope
                  .map((id) => sourceNames[id] || id)
                  .join(", ")}
              >
                <Badge variant="neutral">
                  sources: {sourceScope.length}
                </Badge>
              </span>
            )}
            {typeScope && typeScope.length > 0 && (
              <span title={typeScope.join(", ")}>
                <Badge variant="neutral">
                  types: {typeScope.join("/")}
                </Badge>
              </span>
            )}
          </div>
          <div className="mt-1 text-xs text-fg-muted truncate">
            {s.hostname || "—"}
            {s.version && ` · v${s.version}`}
            {" · last seen "}
            {formatRelative(s.last_seen_at)}
          </div>
          <div
            className="mt-1 text-[10px] text-fg-subtle font-mono truncate"
            title={s.key_fingerprint}
          >
            {s.key_fingerprint.slice(0, 16)}…
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Button size="sm" variant="ghost" onClick={onRotate}>
            Rotate keys
          </Button>
          <Button size="sm" variant="ghost" onClick={onToggle}>
            {s.enabled ? "Disable" : "Enable"}
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={onDelete}
            loading={deleteLoading}
          >
            Delete
          </Button>
        </div>
      </div>
    </li>
  );
}

function JoinTokensList() {
  const { list, revoke } = useScannerClaimTokens();
  if (list.isLoading) {
    return <Spinner />;
  }
  if (list.isError) {
    return (
      <div className="text-xs text-rose-600">
        {list.error instanceof Error
          ? list.error.message
          : "Failed to load join tokens"}
      </div>
    );
  }
  const rows = list.data ?? [];
  if (rows.length === 0) {
    return (
      <Card padding="md">
        <p className="text-xs text-fg-muted">
          No join tokens yet. Click <strong>+ Generate token</strong> to
          mint one.
        </p>
      </Card>
    );
  }
  return (
    <Card padding="none">
      <ul className="divide-y divide-line-subtle">
        {rows.map((t) => (
          <li key={t.id} className="px-4 py-3 flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-medium text-fg truncate">{t.label}</span>
                <Badge variant="neutral">{t.pool}</Badge>
                <StatusBadge status={t.status} />
              </div>
              <div className="mt-1 text-xs text-fg-muted">
                {t.status === "active" &&
                  `expires ${formatRelative(t.expires_at)}`}
                {t.status === "used" &&
                  t.used_at &&
                  `redeemed ${formatRelative(t.used_at)}`}
                {t.status === "expired" && "expired"}
              </div>
            </div>
            {t.status === "active" && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => revoke.mutate(t.id)}
                loading={revoke.isPending && revoke.variables === t.id}
              >
                Revoke
              </Button>
            )}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  const variant: Parameters<typeof Badge>[0]["variant"] =
    status === "active" ? "neutral" : "neutral";
  return <Badge variant={variant}>{status}</Badge>;
}

function PendingClaimsSection() {
  const { value: discoveryEnabled } = useServerSetting<boolean>(
    "discovery_enabled", false,
  );
  const { list } = useDiscoveryRequests();

  const pending = (list.data ?? []).filter((r) => r.status === "pending");

  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <CardHeader title="Pending claims" />
        <DiscoveryToggle />
      </div>
      {!discoveryEnabled ? (
        <Card padding="md">
          <p className="text-sm text-fg">
            Discovery is off. Scanners need a join token to register.
          </p>
          <p className="text-xs text-fg-muted mt-2">
            Turn it on to let scanners self-register and queue here for
            your approval. Useful when you don't want to copy a token —
            the scanner just shows a pairing code in its logs.
          </p>
        </Card>
      ) : pending.length === 0 ? (
        <Card padding="md">
          <p className="text-xs text-fg-muted">
            No scanners are waiting for approval.
          </p>
        </Card>
      ) : (
        <Card padding="none">
          <ul className="divide-y divide-line-subtle">
            {pending.map((r) => (
              <PendingClaimRow key={r.id} request={r} />
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

function ManualKeyForm({
  onIssued,
}: { onIssued: (data: ScannerCreated) => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [pool, setPool] = useState("default");
  const createMut = useMutation<
    ScannerCreated, Error, { name: string; pool: string }
  >({
    mutationFn: (body) => api.post<ScannerCreated>("/scanners", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
  });

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const result = await createMut.mutateAsync({
      name: name.trim(),
      pool: pool.trim() || "default",
    });
    onIssued(result);
    setName("");
    setPool("default");
  }

  return (
    <form
      onSubmit={handleCreate}
      className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end"
    >
      <Input
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="amsterdam-1"
        required
      />
      <Input
        label="Pool"
        value={pool}
        onChange={(e) => setPool(e.target.value)}
        placeholder="default"
      />
      <Button type="submit" loading={createMut.isPending}>
        Register with key
      </Button>
      {createMut.isError && (
        <p className="sm:col-span-3 text-xs text-rose-600">
          {createMut.error instanceof Error
            ? createMut.error.message
            : "Failed to create"}
        </p>
      )}
    </form>
  );
}

function KeyIssuedModal({
  data, onClose,
}: { data: ScannerCreated; onClose: () => void }) {
  function downloadKey() {
    const blob = new Blob([data.private_key_pem], { type: "application/x-pem-file" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${data.name}.key`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-surface rounded-lg shadow-xl border border-line w-full max-w-2xl p-5"
      >
        <h2 className="text-base font-semibold text-fg mb-1">
          Scanner registered: {data.name}
        </h2>
        <p className="text-xs text-amber-700 mb-3">
          This is the only time the private key is shown. Save it now — the
          api stores only the public key. If you lose this, rotate to mint a
          new pair.
        </p>
        <dl className="space-y-2 text-xs mb-4">
          <div className="flex gap-2">
            <dt className="w-32 text-fg-muted">Scanner ID</dt>
            <dd className="font-mono text-fg break-all">{data.id}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-32 text-fg-muted">Pool</dt>
            <dd className="text-fg">{data.pool}</dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-32 text-fg-muted">Fingerprint</dt>
            <dd className="font-mono text-fg break-all">{data.key_fingerprint}</dd>
          </div>
        </dl>
        <label className="block text-xs font-medium text-fg-muted mb-1">
          Private key (PEM, PKCS8)
        </label>
        <textarea
          readOnly
          className="w-full h-44 px-3 py-2 font-mono text-[11px] border border-line rounded bg-app text-fg"
          value={data.private_key_pem}
        />
        <div className="flex justify-between items-center mt-3 text-xs text-fg-muted">
          <span>
            Run on the scanner host:{" "}
            <code className="font-mono">
              akashic-scanner agent --scanner-id={data.id} --key=./{data.name}.key --api=https://...
            </code>
          </span>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <Button variant="ghost" onClick={downloadKey}>
            Download .key
          </Button>
          <Button onClick={onClose}>I've saved the key</Button>
        </div>
      </div>
    </div>
  );
}

function RotateConfirm({
  scanner, onCancel, onConfirm, pending,
}: {
  scanner: Scanner;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  return (
    <div
      onClick={onCancel}
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-surface rounded-lg shadow-xl border border-line w-full max-w-md p-5"
      >
        <h2 className="text-base font-semibold text-fg mb-2">
          Rotate keys for "{scanner.name}"?
        </h2>
        <p className="text-sm text-fg-muted mb-4">
          A new keypair is generated and the old private key stops
          authenticating immediately. Replace the key file on the scanner
          host with the new private key — until you do, the agent will get
          401s on every call.
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="danger" onClick={onConfirm} loading={pending}>
            Rotate
          </Button>
        </div>
      </div>
    </div>
  );
}
