/**
 * Preview-then-confirm flow for re-attaching orphaned entries
 * (source_id IS NULL) into a freshly-created source.
 *
 * UX intent:
 *   - Operator clicks "Recover orphans" on the source detail panel.
 *   - Modal runs the matcher in dry-run mode and shows a breakdown:
 *     ✓ matched (will be re-attached on confirm)
 *     ⚠ conflicts (path matches but hash differs — left alone)
 *     ⊘ ambiguous (multiple orphans share a path — left alone)
 *   - Strategy radio lets the operator switch path-only vs strict.
 *   - On confirm, the matcher commits — orphans land on this source
 *     with their tags, version history, and audit trail intact.
 */
import { useState } from "react";

import { Button, ModalShell, Spinner } from "../ui";
import {
  useReattachCommit, useReattachDryRun,
  type ReattachStrategy,
} from "../../hooks/useOrphanRecovery";

interface Props {
  open: boolean;
  sourceId: string;
  sourceName: string;
  onClose: () => void;
}

export function RecoverOrphansModal({
  open, sourceId, sourceName, onClose,
}: Props) {
  const [strategy, setStrategy] = useState<ReattachStrategy>("path");
  const dryRunQ = useReattachDryRun(sourceId, strategy, open);
  const commit = useReattachCommit(sourceId);

  const summary = dryRunQ.data;

  async function handleConfirm() {
    try {
      await commit.mutateAsync({ strategy });
      onClose();
    } catch {
      /* surfaced via commit.isError below */
    }
  }

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      blocking={commit.isPending}
      maxWidth="xl"
      ariaLabelledBy="recover-orphans-title"
    >
      <div className="px-5 py-3 border-b border-line">
        <h2
          id="recover-orphans-title"
          className="text-base font-semibold text-fg"
        >
          Recover orphans into "{sourceName}"
        </h2>
        <p className="text-xs text-fg-muted mt-1">
          Match orphaned entries (whose original source was deleted)
          against the freshly-scanned files of this source.
          Re-attached entries keep their tags, version history, and
          audit trail.
        </p>
      </div>

      <div className="p-5 space-y-3">
        <div>
          <p className="text-xs font-medium text-fg-muted mb-2">
            Matching strategy
          </p>
          <div className="space-y-2">
            <StrategyOption
              value="path"
              checked={strategy === "path"}
              onChange={() => setStrategy("path")}
              title="Path"
              description="Match on path + name + kind. Use when the new source points at the same data and you trust the file at this path is still the same file."
            />
            <StrategyOption
              value="path_and_hash"
              checked={strategy === "path_and_hash"}
              onChange={() => setStrategy("path_and_hash")}
              title="Path + content hash"
              description="Stricter: also require content hash to match. Orphans without a hash never match — run a full scan first if you want this mode."
            />
          </div>
        </div>

        <div className="border border-line rounded-lg p-3 bg-app">
          {dryRunQ.isLoading ? (
            <div className="flex items-center gap-2 text-xs text-fg-muted">
              <Spinner /> Computing matches…
            </div>
          ) : dryRunQ.isError ? (
            <p className="text-xs text-rose-600">
              {dryRunQ.error instanceof Error
                ? dryRunQ.error.message
                : "Failed to compute matches"}
            </p>
          ) : summary ? (
            <SummaryBreakdown summary={summary} />
          ) : null}
        </div>

        {commit.isError && (
          <p className="text-xs text-rose-600">
            {commit.error instanceof Error
              ? commit.error.message
              : "Recovery failed"}
          </p>
        )}
      </div>

      <div className="px-5 py-3 border-t border-line flex justify-end gap-2">
        <Button
          variant="ghost"
          onClick={onClose}
          disabled={commit.isPending}
        >
          Cancel
        </Button>
        <Button
          onClick={handleConfirm}
          loading={commit.isPending}
          disabled={!summary || summary.matched === 0}
          reserveLabel="Recover 9,999"
          className="tabular-nums"
        >
          Recover {summary?.matched ?? 0}
        </Button>
      </div>
    </ModalShell>
  );
}

function SummaryBreakdown({
  summary,
}: { summary: { matched: number; conflicts: number; ambiguous: number } }) {
  return (
    <ul className="text-xs space-y-1.5 text-fg">
      <li>
        <span className="text-emerald-600">✓</span>{" "}
        <strong>{summary.matched.toLocaleString()}</strong> orphan
        {summary.matched === 1 ? "" : "s"} match by the chosen strategy
        and will be re-attached.
      </li>
      <li>
        <span className="text-amber-600">⚠</span>{" "}
        <strong>{summary.conflicts.toLocaleString()}</strong>{" "}
        path-match{summary.conflicts === 1 ? "" : "es"} where the
        content hash differs — the file changed since indexing.
        These are NOT auto-recovered.
      </li>
      <li>
        <span className="text-fg-subtle">⊘</span>{" "}
        <strong>{summary.ambiguous.toLocaleString()}</strong>{" "}
        ambiguous case{summary.ambiguous === 1 ? "" : "s"} where
        multiple orphans share a path. Left alone — pick one
        manually if you want to recover.
      </li>
    </ul>
  );
}

function StrategyOption({
  value, checked, onChange, title, description,
}: {
  value: ReattachStrategy;
  checked: boolean;
  onChange: () => void;
  title: string;
  description: string;
}) {
  // Match the contrast pattern in DeleteSourceModal: tint the option +
  // force a high-contrast text colour over the tint so the description
  // isn't washed out by text-fg-muted on bg-blue-50.
  const checkedClasses =
    "border-blue-500 bg-blue-50 text-blue-900 dark:bg-blue-500/15 dark:text-blue-100";
  const idleClasses = "border-line hover:bg-app text-fg";
  return (
    <label
      className={`block border rounded-lg p-3 cursor-pointer transition-colors ${
        checked ? checkedClasses : idleClasses
      }`}
    >
      <div className="flex items-start gap-2">
        <input
          type="radio"
          name="reattach-strategy"
          value={value}
          checked={checked}
          onChange={onChange}
          className="mt-1"
        />
        <div>
          <div className="text-sm font-medium">{title}</div>
          <div className="text-xs mt-1 opacity-80">{description}</div>
        </div>
      </div>
    </label>
  );
}
