import { Badge, Button, Card, CardHeader } from "../ui";
import { formatDateTime } from "../../lib/format";
import type { components } from "../../api/schema";

type S = components["schemas"];

const ORIGIN_LABEL: Record<string, string> = {
  ad: "Active Directory",
  authentik: "Authentik",
  akashic_binding: "Akashic binding",
  email: "Email",
  builtin: "Built-in",
};

const MISSING_LABEL: Record<string, string> = { sid: "No SID", posix: "No POSIX id" };

/** The API refreshes only people with an AD or Authentik record; anything
 * else answers 400, so the button is not offered. */
export function canRefresh(identity: S["IdentityResult"] | null): boolean {
  return (
    identity !== null &&
    identity.kind === "person" &&
    identity.backends.some((b) => b.backend === "ad" || b.backend === "authentik")
  );
}

export function TokensUsedPanel({
  tokens,
  expansion,
  identity,
  onRefresh,
  refreshing,
  refreshError,
}: {
  tokens: S["TokenUsed"][];
  expansion: S["AccessExpansion"] | null;
  identity: S["IdentityResult"] | null;
  onRefresh?: () => void;
  refreshing?: boolean;
  refreshError?: string | null;
}) {
  return (
    <Card padding="md">
      <CardHeader
        title="Tokens used"
        description="Every principal token this lookup evaluated, and where it came from."
        action={
          canRefresh(identity) && onRefresh ? (
            <Button variant="secondary" size="sm" onClick={onRefresh} loading={refreshing}>
              Refresh
            </Button>
          ) : undefined
        }
      />
      {expansion?.status === "partial" && (
        <div className="mb-3 border border-amber-300 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-700/40 rounded p-3 text-sm text-amber-800 dark:text-amber-200">
          <strong>Group expansion incomplete.</strong>
          {expansion.detail && <span> {expansion.detail}</span>}
        </div>
      )}
      {expansion?.status === "cached" && (
        <p className="mb-3 text-sm text-fg-muted">
          Group membership as of {expansion.cached_at ? formatDateTime(expansion.cached_at) : "an unknown time"}.
          {expansion.detail && <span> {expansion.detail}</span>}
        </p>
      )}
      {expansion?.status === "failed" && expansion.detail && (
        <p className="mb-3 text-sm text-fg-muted">{expansion.detail}</p>
      )}
      {expansion && expansion.missing_identity_kinds.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2">
          {expansion.missing_identity_kinds.map((k) => (
            <Badge key={k} variant="scanning">
              {MISSING_LABEL[k] ?? k}
            </Badge>
          ))}
        </div>
      )}
      {refreshError && (
        <div role="alert" className="mb-3 border border-rose-300 bg-rose-50 dark:bg-rose-950/30 dark:border-rose-700/40 rounded p-3 text-sm text-rose-800 dark:text-rose-200">
          {refreshError}
        </div>
      )}
      <ul className="space-y-1.5">
        {tokens.map((t) => (
          <li key={`${t.origin}:${t.token}`} className="flex items-center gap-3 text-sm">
            <Badge variant="neutral">{ORIGIN_LABEL[t.origin] ?? t.origin}</Badge>
            <code className="font-mono text-xs text-fg-subtle break-all">{t.token}</code>
            {t.name && <span className="text-fg">{t.name}</span>}
          </li>
        ))}
      </ul>
    </Card>
  );
}
