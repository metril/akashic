/**
 * Admin: register scanner agents, mint keypairs, see online status.
 *
 * The api never persists private keys — when a scanner is created
 * (POST /api/scanners) it returns the private key inline ONCE. We
 * render it in a modal with a single-shot copy button + a clear
 * "won't see this again" warning. The private key is also offered as
 * a downloadable .pem so operators can drop it onto the scanner host
 * directly.
 *
 * Pool routing is permissive: scanners with a pool tag claim
 * matching sources; sources with no preferred_pool match any pool.
 * "Rotate" mints a new keypair and the OLD private key stops
 * authenticating immediately.
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

  const createMut = useMutation<ScannerCreated, Error, { name: string; pool: string }>(
    {
      mutationFn: (body) => api.post<ScannerCreated>("/scanners", body),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["scanners"] }),
    },
  );

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

  const [name, setName] = useState("");
  const [pool, setPool] = useState("default");
  const [issued, setIssued] = useState<ScannerCreated | null>(null);
  const [rotateConfirm, setRotateConfirm] = useState<Scanner | null>(null);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    const result = await createMut.mutateAsync({
      name: name.trim(),
      pool: pool.trim() || "default",
    });
    setIssued(result);
    setName("");
    setPool("default");
  }

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
      description="Registered agents. Scans queue into the api and a matching scanner picks them up."
      width="default"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        <div className="md:col-span-2">
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
                title="No scanners registered"
                description="Mint one on the right, then run akashic-scanner agent on a host that can reach your sources."
              />
            </Card>
          ) : (
            <Card padding="none">
              <ul className="divide-y divide-line-subtle">
                {(scannersQ.data ?? []).map((s) => (
                  <li key={s.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span
                            className={`size-2 rounded-full ${
                              s.online ? "bg-emerald-500" : "bg-fg-subtle"
                            }`}
                            aria-label={s.online ? "online" : "offline"}
                          />
                          <span className="font-medium text-fg truncate">{s.name}</span>
                          <Badge variant="neutral">{s.pool}</Badge>
                          {!s.enabled && <Badge variant="neutral">disabled</Badge>}
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
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setRotateConfirm(s)}
                        >
                          Rotate keys
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            patchMut.mutate({ id: s.id, enabled: !s.enabled })
                          }
                        >
                          {s.enabled ? "Disable" : "Enable"}
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={() => handleDelete(s)}
                          loading={deleteMut.isPending && deleteMut.variables === s.id}
                        >
                          Delete
                        </Button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>

        <Card padding="md">
          <CardHeader title="Register a scanner" />
          <form onSubmit={handleCreate} className="space-y-3">
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
              hint="Permissive: a source with no pool can be claimed by any scanner; a source with pool 'site-amsterdam' only matches scanners in that pool."
            />
            <Button type="submit" loading={createMut.isPending} className="w-full">
              Register
            </Button>
            {createMut.isError && (
              <p className="text-xs text-rose-600">
                {createMut.error instanceof Error
                  ? createMut.error.message
                  : "Failed to create"}
              </p>
            )}
          </form>
        </Card>
      </div>

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
