import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../api/client";
import { Button, Input } from "../ui";
import { ModalShell } from "../ui/ModalShell";

interface BulkTagDialogProps {
  open: boolean;
  onClose: () => void;
  entryIds: string[];
  /** Called after a successful apply, after entry-drawer queries are
   *  invalidated. Page-specific cleanup (e.g. clearing the selection)
   *  goes here. */
  onApplied?: () => void;
}

/**
 * Modal: enter comma-separated tag names, POST /api/tags/bulk-apply.
 * Shared by the Search and Browse pages — both surface a "Tag
 * selected (N)" action when the user has multiple entries selected.
 *
 * Tagging a directory inherits to every descendant on the server
 * (see docs/tags.md); the dialog warns the user implicitly via the
 * generic count.
 */
export function BulkTagDialog({
  open,
  onClose,
  entryIds,
  onApplied,
}: BulkTagDialogProps) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");

  const applyMut = useMutation({
    mutationFn: (tags: string[]) =>
      api.post<void>("/tags/bulk-apply", {
        entry_ids: entryIds,
        tags,
      }),
    onSuccess: () => {
      // Each tagged entry's drawer query needs to refresh.
      for (const id of entryIds) {
        queryClient.invalidateQueries({ queryKey: ["entry", id] });
      }
      setDraft("");
      onClose();
      onApplied?.();
    },
  });

  function submit() {
    const items = draft
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (items.length > 0) applyMut.mutate(items);
  }

  // Migrated to ModalShell (review W-I4) so this dialog inherits the
  // shared ESC handling + focus trap + previously-focused-element
  // restoration on close.
  return (
    <ModalShell open={open} onClose={onClose} maxWidth="md">
      <div className="p-5">
        <h2 className="text-base font-semibold text-fg mb-2">
          Tag {entryIds.length} selected
        </h2>
        <p className="text-xs text-fg-muted mb-3">
          Comma-separated. Tagging a directory inherits to every
          descendant.
        </p>
        <Input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="archive, fy26"
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <div className="flex justify-end gap-2 mt-4">
          <Button
            variant="ghost"
            onClick={() => {
              onClose();
              setDraft("");
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={submit}
            loading={applyMut.isPending}
            disabled={!draft.trim()}
          >
            Apply
          </Button>
        </div>
      </div>
    </ModalShell>
  );
}
