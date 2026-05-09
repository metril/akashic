import { EmptyState } from "./EmptyState";
import { Spinner } from "./Spinner";

interface SectionStateProps {
  loading?: boolean;
  error?: unknown;
  empty?: boolean;
  /** Empty-state heading (e.g. "No credentials yet"). */
  emptyTitle?: string;
  /** Empty-state body copy with optional call-to-action verbiage. */
  emptyMessage?: string;
  /** Primary action surfaced inside the empty-state card (button/link). */
  emptyAction?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Standardised loading / error / empty state for a settings card body.
 *
 * Settings sub-pages shipped v0.25.0 with each page rendering its own
 * if/else tree — different paddings (py-8 vs py-12), different error
 * shapes (rose-600 div vs Card vs alert), different empty-state copy
 * patterns. This wraps all three so callers stay focused on the
 * happy-path content. Apply via:
 *
 *   <SectionState
 *     loading={query.isLoading}
 *     error={query.error}
 *     empty={items.length === 0}
 *     emptyTitle="No credentials yet"
 *     emptyMessage="Reusable credential profiles you create here can attach to many hosts."
 *     emptyAction={<Button onClick={openCreate}>+ New profile</Button>}
 *   >
 *     <list of items />
 *   </SectionState>
 */
export function SectionState({
  loading,
  error,
  empty,
  emptyTitle,
  emptyMessage,
  emptyAction,
  children,
}: SectionStateProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-fg-subtle">
        <Spinner />
      </div>
    );
  }
  if (error) {
    const message =
      error instanceof Error
        ? error.message
        : typeof error === "string"
        ? error
        : "Something went wrong";
    return (
      <div className="rounded-md p-3 text-sm bg-rose-50 text-rose-900 dark:bg-rose-500/15 dark:text-rose-100">
        {message}
      </div>
    );
  }
  if (empty) {
    return (
      <EmptyState
        title={emptyTitle ?? "Nothing to show"}
        description={emptyMessage}
        action={emptyAction}
      />
    );
  }
  return <>{children}</>;
}
