/**
 * Mirror-link footprint in the EntryDetail drawer. App side (Immich /
 * Paperless): the backing files on disk and their total. Backing side:
 * the app entry this file is linked to.
 */
import type { EntryDetail } from "../../api/types";
import { formatBytes } from "../../lib/format";
import { useEntryDetail } from "../../hooks/useEntryDetail";

export function MirrorLinks({ entry }: { entry: EntryDetail }) {
  const { openEntry } = useEntryDetail();
  const files = entry.backing_files ?? [];

  if (entry.linked_entry) {
    const l = entry.linked_entry;
    return (
      <p className="text-sm text-fg">
        Linked to{" "}
        <button
          type="button"
          className="text-accent-600 hover:underline"
          onClick={() => openEntry(l.id)}
        >
          {l.name}
        </button>{" "}
        ({l.link_role}) <span className="text-xs text-fg-subtle">in {l.source_name}</span>
      </p>
    );
  }

  if (files.length === 0) return null;
  return (
    <div className="space-y-2">
      {entry.backing_total_bytes != null && (
        <p className="text-sm text-fg font-medium">
          On disk: {formatBytes(entry.backing_total_bytes)}
        </p>
      )}
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-fg-muted">
            <th className="font-normal pb-1">File</th>
            <th className="font-normal pb-1">Role</th>
            <th className="font-normal pb-1 text-right">Size</th>
          </tr>
        </thead>
        <tbody>
          {files.map((f) => (
            <tr key={f.id} className="border-t border-line-subtle">
              <td className="py-1 pr-2 min-w-0 break-all">
                <button
                  type="button"
                  className="text-left text-accent-600 hover:underline"
                  onClick={() => openEntry(f.id)}
                  title={`${f.source_name}: ${f.path}`}
                >
                  {f.path}
                </button>
                <div className="text-xs text-fg-subtle">{f.source_name}</div>
              </td>
              <td className="py-1 pr-2 whitespace-nowrap">{f.link_role}</td>
              <td className="py-1 text-right tabular-nums whitespace-nowrap">
                {formatBytes(f.size_bytes)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
