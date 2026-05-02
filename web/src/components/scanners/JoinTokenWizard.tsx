/**
 * Three-step wizard for minting + handing off a join token.
 *
 *   1. Configure  — label, pool, TTL, optional restrictions
 *   2. Copy & run — show plaintext token + tabbed paste-target snippets
 *   3. Live confirmation — wait for the scanner.claim_redeemed event
 *      whose token_id matches; flip to a success card on match.
 *
 * The token is only ever shown in step 2; closing the wizard before
 * the scanner registers leaves the token row Active in the list, so
 * the operator can find the snippets again later.
 */
import { useEffect, useMemo, useState } from "react";

import { Badge, Button, Card, CardHeader, Input } from "../ui";
import { useScannerClaimTokens } from "../../hooks/useScannerClaimTokens";
import { useScannersStreamEvents } from "../../hooks/useScannersStreamEvents";
import { useSources } from "../../hooks/useSources";
import type { ClaimTokenCreated } from "../../hooks/useScannerClaimTokens";
import { SnippetTabs } from "./SnippetTabs";

const TTL_CHOICES: { label: string; minutes: number }[] = [
  { label: "15 minutes", minutes: 15 },
  { label: "1 hour", minutes: 60 },
  { label: "24 hours", minutes: 60 * 24 },
  { label: "7 days", minutes: 60 * 24 * 7 },
];

const SCAN_TYPES = ["incremental", "full"] as const;

interface Props {
  onClose: () => void;
}

export function JoinTokenWizard({ onClose }: Props) {
  const sourcesQ = useSources();
  const { create } = useScannerClaimTokens();

  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [label, setLabel] = useState("");
  const [pool, setPool] = useState("default");
  const [ttlMin, setTtlMin] = useState(60);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [allowedSources, setAllowedSources] = useState<Set<string>>(new Set());
  const [allowedTypes, setAllowedTypes] = useState<Set<string>>(new Set());
  const [issued, setIssued] = useState<ClaimTokenCreated | null>(null);
  const [redeemed, setRedeemed] = useState<{
    scanner_id: string; scanner_name: string; pool: string;
  } | null>(null);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!label.trim()) return;
    const body = {
      label: label.trim(),
      pool: pool.trim() || "default",
      ttl_minutes: ttlMin,
      ...(allowedSources.size > 0
        ? { allowed_source_ids: Array.from(allowedSources) }
        : {}),
      ...(allowedTypes.size > 0
        ? { allowed_scan_types: Array.from(allowedTypes) }
        : {}),
    };
    const result = await create.mutateAsync(body);
    setIssued(result);
    setStep(2);
  }

  // Step 3: subscribe to /ws/scanners and flip to success on a
  // matching claim_redeemed event.
  useScannersStreamEvents((event) => {
    if (
      step === 3 &&
      event.kind === "scanner.claim_redeemed" &&
      issued &&
      event.token_id === issued.id
    ) {
      setRedeemed({
        scanner_id: event.scanner_id,
        scanner_name: event.scanner_name,
        pool: event.pool,
      });
    }
  });

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-surface rounded-lg shadow-xl border border-line w-full max-w-2xl"
      >
        <div className="px-5 py-3 border-b border-line flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">
            Generate join token
          </h2>
          <span className="text-xs text-fg-muted">Step {step} of 3</span>
        </div>

        {step === 1 && (
          <form onSubmit={handleGenerate} className="p-5 space-y-3">
            <Input
              label="Label"
              required
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="homelab-nas"
              hint="Helps you identify this token in the list. The scanner's initial name is derived from it."
            />
            <Input
              label="Pool"
              value={pool}
              onChange={(e) => setPool(e.target.value)}
              placeholder="default"
            />
            <div>
              <label className="block text-xs font-medium text-fg-muted mb-1.5">
                Expires in
              </label>
              <select
                value={String(ttlMin)}
                onChange={(e) => setTtlMin(Number(e.target.value))}
                className="w-full h-10 rounded-lg border border-line bg-surface px-3 text-sm text-fg focus:outline-none focus:ring-2 focus:ring-accent-500/30 focus:border-accent-500"
              >
                {TTL_CHOICES.map((c) => (
                  <option key={c.minutes} value={c.minutes}>
                    {c.label}
                  </option>
                ))}
              </select>
            </div>

            <details
              open={scopeOpen}
              onToggle={(e) => setScopeOpen((e.target as HTMLDetailsElement).open)}
              className="border border-line rounded p-3"
            >
              <summary className="cursor-pointer text-xs font-medium text-fg-muted">
                Restrictions (optional)
              </summary>
              <div className="mt-3 space-y-3">
                <div>
                  <p className="text-xs font-medium text-fg-muted mb-1">
                    Allowed sources
                  </p>
                  <p className="text-[11px] text-fg-subtle mb-2">
                    Unchecked = scanner may claim any source.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {(sourcesQ.data ?? []).length === 0 && (
                      <span className="text-xs text-fg-subtle">
                        No sources to choose from.
                      </span>
                    )}
                    {(sourcesQ.data ?? []).map((s) => {
                      const checked = allowedSources.has(s.id);
                      return (
                        <label
                          key={s.id}
                          className={`text-xs px-2 py-1 rounded border cursor-pointer ${
                            checked
                              ? "bg-blue-50 border-blue-400 text-blue-700"
                              : "border-line text-fg-muted hover:bg-app"
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={checked}
                            onChange={() => {
                              const next = new Set(allowedSources);
                              if (checked) next.delete(s.id);
                              else next.add(s.id);
                              setAllowedSources(next);
                            }}
                          />
                          {s.name}
                        </label>
                      );
                    })}
                  </div>
                </div>
                <div>
                  <p className="text-xs font-medium text-fg-muted mb-1">
                    Allowed scan types
                  </p>
                  <p className="text-[11px] text-fg-subtle mb-2">
                    Unchecked = scanner may claim any type.
                  </p>
                  <div className="flex gap-2">
                    {SCAN_TYPES.map((t) => {
                      const checked = allowedTypes.has(t);
                      return (
                        <label
                          key={t}
                          className={`text-xs px-2 py-1 rounded border cursor-pointer ${
                            checked
                              ? "bg-blue-50 border-blue-400 text-blue-700"
                              : "border-line text-fg-muted hover:bg-app"
                          }`}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={checked}
                            onChange={() => {
                              const next = new Set(allowedTypes);
                              if (checked) next.delete(t);
                              else next.add(t);
                              setAllowedTypes(next);
                            }}
                          />
                          {t}
                        </label>
                      );
                    })}
                  </div>
                </div>
              </div>
            </details>

            {create.isError && (
              <p className="text-xs text-rose-600">
                {create.error instanceof Error
                  ? create.error.message
                  : "Failed to mint token"}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-line">
              <Button variant="ghost" type="button" onClick={onClose}>
                Cancel
              </Button>
              <Button type="submit" loading={create.isPending}>
                Generate →
              </Button>
            </div>
          </form>
        )}

        {step === 2 && issued && (
          <Step2 issued={issued} onBack={() => setStep(1)} onNext={() => setStep(3)} />
        )}

        {step === 3 && issued && (
          <Step3
            issued={issued}
            redeemed={redeemed}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
}

function Step2({
  issued, onBack, onNext,
}: {
  issued: ClaimTokenCreated;
  onBack: () => void;
  onNext: () => void;
}) {
  const [tokenCopied, setTokenCopied] = useState(false);

  function copyToken() {
    navigator.clipboard.writeText(issued.token).then(() => {
      setTokenCopied(true);
      window.setTimeout(() => setTokenCopied(false), 1500);
    });
  }

  return (
    <div className="p-5 space-y-3">
      <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
        ⚠ Copy this now. The token is shown only once. The api stores
        only its hash.
      </p>
      <div className="flex items-center gap-2 bg-app border border-line rounded px-3 py-2">
        <code className="font-mono text-xs flex-1 break-all text-fg">
          {issued.token}
        </code>
        <Button size="sm" variant="ghost" onClick={copyToken}>
          {tokenCopied ? "Copied!" : "📋"}
        </Button>
      </div>
      <SnippetTabs snippets={issued.snippets} />
      <p className="text-xs text-fg-muted">
        Run this on the host where the scanner will live.
      </p>
      <div className="flex justify-end gap-2 pt-2 border-t border-line">
        <Button variant="ghost" onClick={onBack}>
          ← Back
        </Button>
        <Button onClick={onNext}>I've started it →</Button>
      </div>
    </div>
  );
}

function Step3({
  issued, redeemed, onClose,
}: {
  issued: ClaimTokenCreated;
  redeemed: { scanner_id: string; scanner_name: string; pool: string } | null;
  onClose: () => void;
}) {
  // Reactive countdown to the token's TTL.
  const expiresMs = useMemo(
    () => new Date(issued.expires_at).getTime(),
    [issued.expires_at],
  );
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const remaining = Math.max(0, expiresMs - now);
  const mins = Math.floor(remaining / 60_000);
  const secs = Math.floor((remaining % 60_000) / 1000);

  if (redeemed) {
    return (
      <div className="p-5 space-y-3">
        <Card padding="md">
          <CardHeader title="Scanner registered ✓" />
          <p className="text-sm text-fg">
            <span className="font-medium">{redeemed.scanner_name}</span>{" "}
            is now active in pool <Badge variant="neutral">{redeemed.pool}</Badge>.
          </p>
          <p className="text-xs text-fg-muted mt-1">
            Scanner ID:{" "}
            <code className="font-mono text-[11px]">{redeemed.scanner_id}</code>
          </p>
        </Card>
        <div className="flex justify-end pt-2 border-t border-line">
          <Button onClick={onClose}>Done</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-5 space-y-3">
      <div className="flex flex-col items-center text-center py-6 space-y-3">
        <div className="w-10 h-10 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-fg">
          Waiting for the scanner to register…
        </p>
        <p className="text-xs text-fg-muted">
          It can take up to a minute after <code>docker compose up</code>.
          This page will update automatically.
        </p>
        <p className="text-xs text-fg-subtle">
          Token expires in {mins}m {secs}s
        </p>
      </div>
      <div className="flex justify-end pt-2 border-t border-line">
        <Button variant="ghost" onClick={onClose}>
          I'll check back later
        </Button>
      </div>
    </div>
  );
}
