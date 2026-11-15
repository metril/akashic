import { useEffect, useMemo, useReducer, useState } from "react";
import { toast } from "sonner";
import { useScopes } from "../../hooks/useScopes";
import { useScopeCatalog, useTokenMutations } from "../../hooks/useApiTokens";
import { useSources } from "../../hooks/useSources";
import { groupScopes, scopeRules, type ExpiryChoice } from "../../lib/scopes";
import {
  canSubmitTokenForm,
  initialTokenForm,
  isLimited,
  toCreateRequest,
  tokenFormReducer,
} from "../../lib/tokenForm";
import { Button, Input, ModalShell, Select } from "../ui";
import { ScopePicker } from "./ScopePicker";
import { TokenReveal } from "./TokenReveal";

const EXPIRY_OPTIONS: { value: ExpiryChoice; label: string }[] = [
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "No expiry" },
];

export function CreateTokenDialog({ onClose }: { onClose: () => void }) {
  const [form, dispatch] = useReducer(tokenFormReducer, initialTokenForm);
  const [secret, setSecret] = useState<string | null>(null);
  const { scopes: mine } = useScopes();
  const catalog = useScopeCatalog();
  const sources = useSources();
  const { create } = useTokenMutations();
  const groups = useMemo(() => groupScopes(catalog.data?.scopes ?? [], mine), [catalog.data, mine]);
  const rules = useMemo(() => scopeRules(catalog.data?.scopes ?? []), [catalog.data]);
  const limited = isLimited(form);
  // Fail closed: nothing can be chosen until the catalog (and so the rules) is known.
  const ready = catalog.data !== undefined;
  useEffect(() => {
    dispatch({ type: "rules", rules });
  }, [rules]);

  // The parent mounts this only while open, so closing drops the form and
  // the one-time secret: neither can be recovered afterwards.
  const close = onClose;

  function submit() {
    create.mutate(toCreateRequest(form), {
      onSuccess: (created) => setSecret(created.token),
      onError: (err) => toast.error(err.message),
    });
  }

  function toggleSource(id: string) {
    const ids = form.sourceIds.includes(id) ? form.sourceIds.filter((s) => s !== id) : [...form.sourceIds, id];
    dispatch({ type: "sources", sourceIds: ids, rules });
  }

  return (
    <ModalShell open onClose={close} blocking={create.isPending || secret !== null} maxWidth="xl" ariaLabelledBy="create-token-title">
      {secret !== null ? (
        <TokenReveal token={secret} onDone={close} />
      ) : (
        <div className="p-5 space-y-4 max-h-[85vh] overflow-y-auto">
          <h2 id="create-token-title" className="text-base font-semibold text-fg">
            New API token
          </h2>
          <Input
            label="Name"
            value={form.name}
            maxLength={100}
            placeholder="e.g. nightly export script"
            onChange={(e) => dispatch({ type: "name", name: e.target.value })}
          />
          {!ready && (
            <div role="status" className="text-xs text-fg-muted flex items-center gap-2">
              {catalog.isError ? (
                <>
                  Couldn't load the scope catalog.
                  <Button size="sm" variant="secondary" onClick={() => catalog.refetch()}>
                    Retry
                  </Button>
                </>
              ) : (
                "Loading scopes…"
              )}
            </div>
          )}
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-fg-muted">Scopes</span>
              <span className="flex gap-2">
                <Button size="sm" variant="secondary" disabled={!ready} onClick={() => dispatch({ type: "preset", preset: "read-only", available: mine, rules })}>
                  Read-only
                </Button>
                <Button size="sm" variant="secondary" disabled={!ready} onClick={() => dispatch({ type: "preset", preset: "full", available: mine, rules })}>
                  Full
                </Button>
              </span>
            </div>
            <ScopePicker
              groups={groups}
              selected={form.scopes}
              sourceLimited={limited}
              onToggle={(scope) => dispatch({ type: "toggle", scope, rules })}
            />
          </div>
          <fieldset className="space-y-1">
            <legend className="text-xs font-medium text-fg-muted">Limit to sources (optional)</legend>
            <p className="text-xs text-fg-subtle">Leave empty for every source. Anything outside the list answers 404.</p>
            <div className="max-h-36 overflow-y-auto rounded border border-line p-2 space-y-1">
              {(sources.data ?? []).map((s) => (
                <label key={s.id} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    className="size-3.5"
                    checked={form.sourceIds.includes(s.id)}
                    disabled={!ready}
                    onChange={() => toggleSource(s.id)}
                    aria-label={`Source ${s.name}`}
                  />
                  {s.name}
                </label>
              ))}
              {sources.data?.length === 0 && <p className="text-xs text-fg-muted">No sources yet.</p>}
            </div>
          </fieldset>
          <Select
            label="Expires"
            value={form.expiry}
            options={EXPIRY_OPTIONS}
            onChange={(e) => dispatch({ type: "expiry", expiry: e.target.value as ExpiryChoice })}
          />
          <div className="flex justify-end gap-2 pt-2 border-t border-line">
            <Button variant="secondary" onClick={close} disabled={create.isPending}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!ready || !canSubmitTokenForm(form)} loading={create.isPending}>
              Create token
            </Button>
          </div>
        </div>
      )}
    </ModalShell>
  );
}
