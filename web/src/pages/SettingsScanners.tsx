/**
 * Four explicit sections, one route:
 *   1. Active scanners — registered agents with scope, search, and
 *      an online-only filter
 *   2. Pending claims — discovery requests waiting for an admin
 *      decision (only renders when discovery is enabled)
 *   3. Add a scanner — primary path: join-token wizard + the list
 *      of already-minted tokens
 *   4. Advanced (legacy manual-key registration) — collapsed by default
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Input,
  ModalShell,
  Page,
  SectionState,
} from "../components/ui";
import { AllowedSourcesModal } from "../components/scanners/AllowedSourcesModal";
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
  const [deleteConfirm, setDeleteConfirm] = useState<Scanner | null>(null);
  const [editSources, setEditSources] = useState<Scanner | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);

  const [filter, setFilter] = useState("");
  const [onlineOnly, setOnlineOnly] = useState(false);

  const filteredScanners = useMemo(() => {
    let rows = scannersQ.data ?? [];
    if (onlineOnly) rows = rows.filter((s) => s.online);
    const q = filter.trim().toLowerCase();
    if (q) {
      rows = rows.filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.pool.toLowerCase().includes(q) ||
          (s.hostname ?? "").toLowerCase().includes(q),
      );
    }
    return rows;
  }, [scannersQ.data, filter, onlineOnly]);
  const totalScanners = scannersQ.data?.length ?? 0;
  const showCount =
    totalScanners > 0 && (filter.trim() !== "" || onlineOnly);

  async function handleRotate(scanner: Scanner) {
    const result = await rotateMut.mutateAsync(scanner.id);
    setIssued(result);
    setRotateConfirm(null);
  }

  async function performDelete(scanner: Scanner) {
    try {
      await deleteMut.mutateAsync(scanner.id);
      setDeleteConfirm(null);
    } catch {
      // mutation error is surfaced inside the dialog row, not the toast
      setDeleteConfirm(null);
    }
  }

  return (
    <Page
      title="Scanners"
      description="Registered agents and the tokens / pending claims that bring new ones online."
      width="default"
    >
      <div className="space-y-6">
        {/* ── 1. Active scanners ──────────────────────────────────── */}
        <Card padding="md">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-fg">Active scanners</h3>
              <p className="text-xs text-fg-muted mt-0.5">
                <em>Online</em> = checked in within the last 90 seconds.
              </p>
            </div>
            {totalScanners > 0 && (
              <Button size="sm" onClick={() => setWizardOpen(true)}>
                + Add scanner
              </Button>
            )}
          </div>
          {totalScanners > 0 && (
            <div className="flex items-center gap-3 mb-3">
              <input
                type="search"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Search by name, pool, or hostname…"
                className="flex-1 rounded-md border border-line bg-surface px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-accent-400 focus:border-accent-400"
              />
              <label className="flex items-center gap-1.5 text-xs cursor-pointer text-fg-muted whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={onlineOnly}
                  onChange={(e) => setOnlineOnly(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-line text-accent-600 focus:ring-accent-400"
                />
                Online only
              </label>
            </div>
          )}
          {showCount && (
            <p className="text-xs text-fg-muted mb-2">
              {filteredScanners.length} of {totalScanners} shown
            </p>
          )}
          <SectionState
            loading={scannersQ.isLoading}
            error={scannersQ.isError ? scannersQ.error : undefined}
            empty={totalScanners === 0}
            emptyTitle="No scanners registered yet"
            emptyMessage="Generate a join token, then run akashic-scanner claim on a host that can reach your sources."
            emptyAction={
              <Button onClick={() => setWizardOpen(true)}>
                + Add scanner
              </Button>
            }
          >
            {filteredScanners.length === 0 ? (
              <p className="text-sm text-fg-muted text-center py-4">
                No scanners match the current filter.
              </p>
            ) : (
              <ul className="divide-y divide-line-subtle border border-line rounded-md">
                {filteredScanners.map((s) => (
                  <ScannerRow
                    key={s.id}
                    scanner={s}
                    onRotate={() => setRotateConfirm(s)}
                    onToggle={() =>
                      patchMut.mutate({ id: s.id, enabled: !s.enabled })
                    }
                    onDelete={() => setDeleteConfirm(s)}
                    deleteLoading={
                      deleteMut.isPending && deleteMut.variables === s.id
                    }
                    onEditSources={() => setEditSources(s)}
                  />
                ))}
              </ul>
            )}
          </SectionState>
        </Card>

        {/* ── 2. Pending claims ───────────────────────────────────── */}
        <PendingClaimsSection />

        {/* ── 3. Add a scanner (join tokens) ──────────────────────── */}
        <Card padding="md">
          <div className="flex items-center justify-between gap-3 mb-3">
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-fg">Add a scanner</h3>
              <p className="text-xs text-fg-muted mt-0.5">
                Recommended path: generate a one-time token and paste it
                into the scanner's run command. The scanner generates its
                own keypair locally — the private key never leaves the host.
              </p>
            </div>
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              + Generate token
            </Button>
          </div>
          <JoinTokensList />
        </Card>

        {/* ── 4. Advanced (manual key registration) ───────────────── */}
        <Card padding="md">
          <details>
            <summary className="cursor-pointer text-sm font-medium text-fg">
              Advanced — register with a server-generated key
            </summary>
            <p className="text-xs text-fg-muted mt-2 mb-3">
              The api generates the keypair and returns the private key
              once. Useful for scripted automation that already depends
              on this flow; for new scanners prefer a join token (above).
            </p>
            <ManualKeyForm onIssued={setIssued} />
          </details>
        </Card>
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
      <ConfirmDialog
        open={deleteConfirm !== null}
        title={
          deleteConfirm
            ? `Delete scanner "${deleteConfirm.name}"?`
            : "Delete scanner?"
        }
        description="Any in-flight scan it was holding will be re-queued. Its private key stops working immediately."
        confirmLabel="Delete"
        destructive
        loading={deleteMut.isPending}
        onConfirm={() => deleteConfirm && performDelete(deleteConfirm)}
        onCancel={() => !deleteMut.isPending && setDeleteConfirm(null)}
      />
      <AllowedSourcesModal
        open={editSources !== null}
        scannerId={editSources?.id ?? null}
        scannerName={editSources?.name ?? ""}
        onClose={() => setEditSources(null)}
      />
    </Page>
  );
}

function ScannerRow({
  scanner: s, onRotate, onToggle, onDelete, deleteLoading, onEditSources,
}: {
  scanner: Scanner;
  onRotate: () => void;
  onToggle: () => void;
  onDelete: () => void;
  deleteLoading: boolean;
  onEditSources: () => void;
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
    <li className="px-4 py-3 flex items-center justify-between gap-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span
            className={`size-2 rounded-full shrink-0 ${
              s.online ? "bg-emerald-500" : "bg-fg-subtle"
            }`}
            aria-label={s.online ? "online" : "offline"}
            title={
              s.online
                ? "Online: scanner agent has checked in within the last 90 seconds"
                : "Offline: scanner agent hasn't checked in for 90+ seconds"
            }
          />
          <span
            className="font-medium text-fg truncate"
            title={`Fingerprint: ${s.key_fingerprint}`}
          >
            {s.name}
          </span>
          <Badge variant="neutral">{s.pool}</Badge>
          <button
            type="button"
            onClick={onEditSources}
            title={
              sourceScope == null
                ? "Allows all sources — click to restrict"
                : sourceScope
                    .map((id) => sourceNames[id] || id)
                    .join(", ")
            }
            className="hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 rounded"
          >
            <Badge variant="neutral">
              sources: {sourceScope == null ? "all" : sourceScope.length}
            </Badge>
          </button>
          {!s.enabled && <Badge variant="neutral">disabled</Badge>}
        </div>
        <div className="mt-1 text-xs text-fg-muted truncate">
          {s.hostname || "—"}
          {s.version && ` · v${s.version}`}
          {" · last seen "}
          {formatRelative(s.last_seen_at)}
          {typeScope && typeScope.length > 0 && (
            <span title={typeScope.join(", ")}>
              {" · types: "}
              {typeScope.join("/")}
            </span>
          )}
        </div>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        <Button size="sm" variant="ghost" onClick={onRotate} title="Rotate keys">
          Rotate
        </Button>
        <Button size="sm" variant="ghost" onClick={onToggle}>
          {s.enabled ? "Disable" : "Enable"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={onDelete}
          loading={deleteLoading}
          className="text-rose-700 hover:text-rose-800 dark:text-rose-300 dark:hover:text-rose-200"
        >
          Delete
        </Button>
      </div>
    </li>
  );
}

function JoinTokensList() {
  const { list, revoke } = useScannerClaimTokens();
  const rows = list.data ?? [];
  return (
    <SectionState
      loading={list.isLoading}
      error={list.isError ? list.error : undefined}
      empty={rows.length === 0}
      emptyTitle="No join tokens yet"
      emptyMessage="Click + Generate token to mint one. Tokens are one-time and expire automatically."
    >
      <ul className="divide-y divide-line-subtle border border-line rounded-md">
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
    </SectionState>
  );
}

function StatusBadge({ status }: { status: string }) {
  // Distinct visual for each token state (review notable). Pre-fix
  // both branches returned "neutral" so active/used/expired tokens
  // looked identical in the list.
  const variant: Parameters<typeof Badge>[0]["variant"] =
    status === "active" ? "online" : status === "used" ? "info" : "neutral";
  return <Badge variant={variant}>{status}</Badge>;
}

function PendingClaimsSection() {
  const { value: discoveryEnabled } = useServerSetting<boolean>(
    "discovery_enabled", false,
  );
  const { list } = useDiscoveryRequests();

  const pending = (list.data ?? []).filter((r) => r.status === "pending");

  // Always render this card so the DiscoveryToggle is reachable —
  // hiding it when discovery is off + no pending claims (as v0.26.0
  // briefly did) leaves admins with no way to turn the feature on.
  return (
    <Card padding="md">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-fg">Auto-discovery & pending claims</h3>
          <p className="text-xs text-fg-muted mt-0.5">
            With auto-discovery on, scanners can self-register without a
            join token and queue here for an admin to approve.
          </p>
        </div>
        <DiscoveryToggle />
      </div>
      {!discoveryEnabled ? (
        <p className="text-xs text-fg-muted">
          Discovery is off. Turn it on (toggle on the right) to let scanners
          self-register; until then they need a join token from the section below.
        </p>
      ) : pending.length === 0 ? (
        <p className="text-xs text-fg-muted text-center py-2">
          No scanners are waiting for approval.
        </p>
      ) : (
        <ul className="divide-y divide-line-subtle border border-line rounded-md">
          {pending.map((r) => (
            <PendingClaimRow key={r.id} request={r} />
          ))}
        </ul>
      )}
    </Card>
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
        <p className="sm:col-span-3 text-xs text-rose-700 dark:text-rose-300" role="alert">
          {createMut.error instanceof Error
            ? createMut.error.message
            : "Couldn't create scanner."}
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
    <ModalShell
      open
      onClose={onClose}
      maxWidth="xl"
      ariaLabelledBy="key-issued-title"
    >
      <div className="p-5">
        <h2 id="key-issued-title" className="text-base font-semibold text-fg mb-1">
          Scanner registered: {data.name}
        </h2>
        <p className="text-xs text-amber-700 dark:text-amber-300 mb-3">
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
          <Button variant="secondary" onClick={downloadKey}>
            Download .key
          </Button>
          <Button onClick={onClose}>I've saved the key</Button>
        </div>
      </div>
    </ModalShell>
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
    <ConfirmDialog
      open
      title={`Rotate keys for "${scanner.name}"?`}
      description="A new keypair is generated and the old private key stops authenticating immediately. Replace the key file on the scanner host with the new private key — until you do, the agent will get 401s on every call."
      confirmLabel="Rotate"
      destructive
      loading={pending}
      onConfirm={onConfirm}
      onCancel={() => !pending && onCancel()}
    />
  );
}
