/**
 * Tag-chip strip in the EntryDetail drawer.
 *
 * Direct tags render as solid chips with a removable "×" affordance.
 * Inherited tags render outlined / faded with no "×"; their tooltip
 * names the source ancestor and clicking the source path navigates
 * to that directory in Browse so the user can manage the tag at its
 * origin.
 *
 * Tags act as filter targets via `FilterableCell crossPage` — left
 * click on a chip jumps to /search filtered to that tag. The "+" /
 * "−" mutation buttons are admin-only; non-admins see the chips but
 * not the controls.
 */
import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import { useAuth } from "../../hooks/useAuth";
import type { EntryDetail, EntryTagAssignment } from "../../types";
import { Button } from "../ui";
import { FilterableCell } from "../ui/FilterableCell";
import { cn } from "../ui/cn";

interface Props {
  entryId: string;
  sourceId: string | null;
  parentPath: string;
  tags: EntryTagAssignment[];
}

export function EntryTags({ entryId, sourceId, parentPath, tags }: Props) {
  const { isAdmin } = useAuth();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [adding, setAdding] = useState(false);

  // Dedup direct + inherited duplicates for layout: when a user has
  // tagged both an ancestor and the entry itself with the same label,
  // we want one chip. Direct wins for the chip's behaviour (it gets
  // the "×" affordance) but we surface the inherited origin in the
  // tooltip for completeness.
  const consolidated = useMemo(() => groupByTag(tags), [tags]);

  // v0.21.0 — optimistic apply/remove. Tag chips appear/vanish on click
  // before the server roundtrip; a failure rolls back via the snapshot
  // captured in onMutate and surfaces a toast. onSettled invalidates so
  // the authoritative state (including any inherited-tag interactions
  // that the local model doesn't fully simulate) catches up.
  const queryKey = useMemo(() => ["entry", entryId] as const, [entryId]);

  const applyMut = useMutation({
    mutationFn: (newTags: string[]) =>
      api.post<void>(`/entries/${entryId}/tags`, { tags: newTags }),
    onMutate: async (newTags: string[]) => {
      await queryClient.cancelQueries({ queryKey });
      const prev = queryClient.getQueryData<EntryDetail>(queryKey);
      if (prev) {
        const have = new Set(prev.tags.map((t) => t.tag));
        const additions: EntryTagAssignment[] = newTags
          .filter((t) => !have.has(t))
          .map((tag) => ({ tag, inherited: false, inherited_from_path: null }));
        queryClient.setQueryData<EntryDetail>(queryKey, {
          ...prev,
          tags: [...prev.tags, ...additions],
        });
      }
      setDraft("");
      setAdding(false);
      return { prev };
    },
    onError: (err, _newTags, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(queryKey, ctx.prev);
      toast.error(`Couldn't apply tag: ${err instanceof Error ? err.message : "unknown error"}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  const removeMut = useMutation({
    mutationFn: (tag: string) =>
      api.delete<void>(`/entries/${entryId}/tags/${encodeURIComponent(tag)}`),
    onMutate: async (tag: string) => {
      await queryClient.cancelQueries({ queryKey });
      const prev = queryClient.getQueryData<EntryDetail>(queryKey);
      if (prev) {
        // Only remove the *direct* assignment — inherited copies of the
        // same tag stay (the API does the same; the chip flips back to
        // its inherited-only rendering on re-fetch).
        queryClient.setQueryData<EntryDetail>(queryKey, {
          ...prev,
          tags: prev.tags.filter((t) => !(t.tag === tag && !t.inherited)),
        });
      }
      return { prev };
    },
    onError: (err, _tag, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(queryKey, ctx.prev);
      toast.error(`Couldn't remove tag: ${err instanceof Error ? err.message : "unknown error"}`);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey });
    },
  });

  function submit() {
    const items = draft
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (items.length > 0) applyMut.mutate(items);
    else setAdding(false);
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {consolidated.length === 0 && !adding && (
        <span className="text-sm text-fg-subtle italic">No tags</span>
      )}

      {consolidated.map(({ tag, direct, inherited }) => (
        <TagChip
          key={tag}
          tag={tag}
          direct={direct}
          inherited={inherited}
          onRemove={
            isAdmin && direct
              ? () => removeMut.mutate(tag)
              : undefined
          }
          sourceId={sourceId}
          parentPath={parentPath}
        />
      ))}

      {isAdmin && !adding && (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full border border-line text-fg-muted hover:text-fg hover:border-line-strong transition-colors"
          title="Add tag"
        >
          + Add
        </button>
      )}

      {isAdmin && adding && (
        <div className="flex items-center gap-1">
          <input
            type="text"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
              if (e.key === "Escape") { setAdding(false); setDraft(""); }
            }}
            placeholder="comma-separated"
            className="px-2 py-0.5 text-xs border border-line-strong rounded-full bg-surface text-fg"
          />
          <Button
            size="sm"
            variant="secondary"
            onClick={submit}
            loading={applyMut.isPending}
          >
            Apply
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => { setAdding(false); setDraft(""); }}
          >
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}

interface ChipProps {
  tag: string;
  direct: boolean;
  inherited: { from: string | null } | null;
  onRemove?: () => void;
  sourceId: string | null;
  parentPath: string;
}

function TagChip({ tag, direct, inherited, onRemove, sourceId, parentPath }: ChipProps) {
  const tooltip = inherited
    ? `Inherited from ${inherited.from ?? "an ancestor"}`
    : `Applied directly`;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full",
        direct
          ? "bg-accent-100 text-accent-800 border border-accent-200"
          : "bg-transparent text-fg-muted border border-dashed border-line-strong",
      )}
      title={tooltip}
    >
      <FilterableCell
        predicate={{ kind: "tag", value: tag }}
        crossPage
        className="!px-0 !mx-0 hover:bg-transparent hover:text-current"
      >
        {tag}
      </FilterableCell>
      {/* The browse link only works while the source still exists.
          Orphaned entries (sourceId == null) keep the chip but drop
          the link — there's no path tree to open. */}
      {inherited?.from && sourceId != null && (
        <Link
          to={`/browse?source=${sourceId}&path=${encodeURIComponent(inherited.from)}`}
          className="text-fg-subtle hover:text-fg-muted"
          title={`Open ${inherited.from} in Browse`}
          onClick={(e) => e.stopPropagation()}
        >
          ↗
        </Link>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="inline-flex items-center justify-center h-5 w-5 rounded-full text-fg-subtle hover:bg-surface-muted hover:text-rose-600 transition-colors ml-0.5"
          aria-label={`Remove tag ${tag}`}
        >
          ×
        </button>
      )}
    </span>
  );
  // parentPath unused right now but kept on the interface so a future
  // "open in current directory" affordance can take it without a
  // breaking signature change.
  void parentPath;
}

interface GroupedTag {
  tag: string;
  direct: boolean;
  inherited: { from: string | null } | null;
}

function groupByTag(tags: EntryTagAssignment[]): GroupedTag[] {
  const map = new Map<string, GroupedTag>();
  for (const t of tags) {
    const cur = map.get(t.tag);
    if (!cur) {
      map.set(t.tag, {
        tag: t.tag,
        direct: !t.inherited,
        inherited: t.inherited ? { from: t.inherited_from_path } : null,
      });
    } else {
      if (!t.inherited) cur.direct = true;
      else if (!cur.inherited) cur.inherited = { from: t.inherited_from_path };
    }
  }
  return Array.from(map.values()).sort((a, b) => a.tag.localeCompare(b.tag));
}
