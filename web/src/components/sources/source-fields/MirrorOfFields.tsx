/**
 * "Mirror of" mappings for backing sources (local / NFS / SMB / S3).
 *
 * Each row links a path prefix on this source to a path prefix inside an
 * Immich or Paperless source, so files on disk are recognised as the
 * backing copies of app entries. Paperless uses an empty remote prefix;
 * S3 local prefixes are key prefixes without a leading slash.
 */
import { Button, Input, Select } from "../../ui";
import { useSources } from "../../../hooks/useSources";
import { validateMirrorOf, type MirrorMapping } from "../../../lib/mirrorConfig";
import type { SourceType } from "../sourceTypes";

interface Props {
  value: MirrorMapping[];
  onChange: (next: MirrorMapping[]) => void;
  backingType: SourceType;
}

export function MirrorOfFields({ value, onChange, backingType }: Props) {
  const sourcesQ = useSources();
  const targets = (sourcesQ.data ?? []).filter(
    (s) => s.type === "immich" || s.type === "paperless",
  );
  const targetTypes = new Map<string, SourceType>(
    targets.map((s) => [s.id, s.type as SourceType]),
  );
  const error = validateMirrorOf(value, backingType, targetTypes);
  const s3 = backingType === "s3";

  const update = (i: number, patch: Partial<MirrorMapping>) =>
    onChange(value.map((m, j) => (j === i ? { ...m, ...patch } : m)));

  return (
    <div className="space-y-3 mt-4">
      <div>
        <h4 className="text-sm font-medium text-fg">Mirror of</h4>
        <p className="text-[11px] text-fg-muted">
          Link files on this source to the Immich or Paperless entries they
          back. Linked files are hidden from search by default and counted
          once in storage totals.
        </p>
      </div>
      {value.map((m, i) => {
        const isPaperless = targetTypes.get(m.source_id) === "paperless";
        return (
          <div key={i} className="space-y-2 rounded-md border border-line p-3">
            <Select
              label="Target source"
              value={m.source_id}
              onChange={(e) => {
                const paperless = targetTypes.get(e.target.value) === "paperless";
                update(i, {
                  source_id: e.target.value,
                  ...(paperless ? { remote_prefix: "" } : {}),
                });
              }}
              options={[
                { value: "", label: "Select a source…" },
                ...targets.map((s) => ({ value: s.id, label: `${s.name} (${s.type})` })),
              ]}
            />
            <Input
              label="Remote prefix"
              value={m.remote_prefix}
              onChange={(e) => update(i, { remote_prefix: e.target.value })}
              placeholder="/data"
              disabled={isPaperless}
            />
            <Input
              label={s3 ? "Local key prefix" : "Local prefix"}
              value={m.local_prefix}
              onChange={(e) => update(i, { local_prefix: e.target.value })}
              placeholder={s3 ? "immich/data" : "/host/mnt/immich/data"}
            />
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => onChange(value.filter((_, j) => j !== i))}
            >
              Remove mapping
            </Button>
          </div>
        );
      })}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={() =>
          onChange([...value, { source_id: "", remote_prefix: "", local_prefix: "" }])
        }
      >
        Add mapping
      </Button>
    </div>
  );
}
