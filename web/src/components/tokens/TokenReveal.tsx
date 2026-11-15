import { useState } from "react";
import { Button } from "../ui";

/**
 * Copy-once display for a freshly created token. The API returns the secret a
 * single time, so this says so loudly and the caller drops it on Done.
 */
export function TokenReveal({ token, onDone }: { token: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard?.writeText(token).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  return (
    <div className="p-5 space-y-3">
      <h2 id="token-reveal-title" className="text-base font-semibold text-fg">
        Token created
      </h2>
      <p
        role="alert"
        className="text-xs text-amber-800 bg-amber-50 border border-amber-200 dark:bg-amber-500/10 dark:text-amber-200 dark:border-amber-500/40 rounded px-3 py-2"
      >
        Copy this token now. It won't be shown again; only its hash is stored. If you lose it, revoke it and create a new one.
      </p>
      <div className="flex items-center gap-2 bg-app border border-line rounded px-3 py-2">
        <code data-testid="token-secret" className="font-mono text-xs flex-1 break-all text-fg">
          {token}
        </code>
        <Button size="sm" variant="secondary" onClick={copy} reserveLabel="Copied!">
          {copied ? "Copied!" : "Copy"}
        </Button>
      </div>
      <p className="text-xs text-fg-muted">
        Use it as <code className="font-mono">Authorization: Bearer {"<token>"}</code>.
      </p>
      <div className="flex justify-end pt-2 border-t border-line">
        <Button onClick={onDone}>I've copied it</Button>
      </div>
    </div>
  );
}
