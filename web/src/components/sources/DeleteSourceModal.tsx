/**
 * Two-flavour delete modal.
 *
 *   - "Delete source only" (default)  → keeps indexed entries; they
 *     survive as orphans (source_id = NULL) and can be re-attached
 *     to a new source later.
 *   - "Delete source AND entries"     → purges both. The actual
 *     files on disk are never touched either way.
 *
 * Shows the entry count up front so the operator understands the
 * blast radius. Default selection is the safer "preserve" option.
 */
import { useState } from "react";

import { Button, ModalShell, Spinner } from "../ui";
import { useSourceEntryCount } from "../../hooks/useSourceEntryCount";

interface Props {
  open: boolean;
  sourceId: string;
  sourceName: string;
  loading: boolean;
  onCancel: () => void;
  onConfirm: (args: { purgeEntries: boolean }) => void;
}

type Flavour = "preserve" | "purge";

export function DeleteSourceModal({
  open, sourceId, sourceName, loading, onCancel, onConfirm,
}: Props) {
  const countQ = useSourceEntryCount(open ? sourceId : null);
  const [flavour, setFlavour] = useState<Flavour>("preserve");

  const count = countQ.data?.count;
  const fmtCount =
    count == null ? "…" : count.toLocaleString();

  return (
    <ModalShell
      open={open}
      onClose={onCancel}
      blocking={loading}
      maxWidth="lg"
      ariaLabelledBy="delete-source-title"
    >
      <div className="px-5 py-3 border-b border-line">
        <h2 id="delete-source-title" className="text-base font-semibold text-fg">
          Delete source "{sourceName}"?
        </h2>
        <p className="text-xs text-fg-muted mt-1">
          This source has{" "}
          <span className="font-medium text-fg">
            {countQ.isLoading ? <Spinner /> : `${fmtCount} indexed files`}
          </span>
          .
        </p>
      </div>

      <div className="p-5 space-y-3">
        <FlavourOption
          id="preserve"
          value="preserve"
          checked={flavour === "preserve"}
          onChange={() => setFlavour("preserve")}
          title="Delete source only"
          description={
            <>
              Keeps the {fmtCount} entries searchable. They'll show
              "(deleted source)" in results. Content fetch is no
              longer possible. You can re-attach them to a new
              source later via the Recover orphans flow.
            </>
          }
        />
        <FlavourOption
          id="purge"
          value="purge"
          checked={flavour === "purge"}
          onChange={() => setFlavour("purge")}
          title="Delete source AND entries"
          description={
            <>
              Also removes the {fmtCount} indexed entries. Original
              files on the storage backend are not touched — only
              Akashic's index of them.
            </>
          }
          destructive
        />
      </div>

      <div className="px-5 py-3 border-t border-line flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel} disabled={loading}>
          Cancel
        </Button>
        <Button
          variant={flavour === "purge" ? "danger" : "primary"}
          loading={loading}
          onClick={() => onConfirm({ purgeEntries: flavour === "purge" })}
        >
          Delete
        </Button>
      </div>
    </ModalShell>
  );
}

function FlavourOption({
  id, value, checked, onChange, title, description, destructive,
}: {
  id: string;
  value: Flavour;
  checked: boolean;
  onChange: () => void;
  title: string;
  description: React.ReactNode;
  destructive?: boolean;
}) {
  // When the option is checked we tint the row and force a darker text
  // colour on top of the tint so the description isn't washed out by
  // text-fg-muted on a near-white background. Dark mode mirrors the
  // pattern with a lower-opacity tint and a higher-luminance text.
  const checkedClasses = destructive
    ? "border-rose-500 bg-rose-50 text-rose-900 dark:bg-rose-500/15 dark:text-rose-100"
    : "border-blue-500 bg-blue-50 text-blue-900 dark:bg-blue-500/15 dark:text-blue-100";
  const idleClasses = "border-line hover:bg-app text-fg";
  return (
    <label
      htmlFor={id}
      className={`block border rounded-lg p-3 cursor-pointer transition-colors ${
        checked ? checkedClasses : idleClasses
      }`}
    >
      <div className="flex items-start gap-2">
        <input
          id={id}
          type="radio"
          name="delete-source-flavour"
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
