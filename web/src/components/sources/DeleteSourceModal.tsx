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

import { Button, Spinner } from "../ui";
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

  if (!open) return null;
  const count = countQ.data?.count;
  const fmtCount =
    count == null ? "…" : count.toLocaleString();

  return (
    <div
      onClick={onCancel}
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-surface rounded-lg shadow-xl border border-line w-full max-w-lg"
      >
        <div className="px-5 py-3 border-b border-line">
          <h2 className="text-base font-semibold text-fg">
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
      </div>
    </div>
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
  return (
    <label
      htmlFor={id}
      className={`block border rounded-lg p-3 cursor-pointer transition-colors ${
        checked
          ? destructive
            ? "border-rose-500 bg-rose-50"
            : "border-blue-500 bg-blue-50"
          : "border-line hover:bg-app"
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
          <div className="text-sm font-medium text-fg">{title}</div>
          <div className="text-xs text-fg-muted mt-1">{description}</div>
        </div>
      </div>
    </label>
  );
}
