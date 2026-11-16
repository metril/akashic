/**
 * "Files on disk" mappings on an Immich / Paperless source.
 *
 * Each row links a path prefix inside this app source to a path prefix on
 * a backing source (local / NFS / SMB / S3), so files on disk are
 * recognised as the backing copies of app entries. Paperless uses an
 * empty remote prefix (input hidden); S3 local prefixes are key prefixes
 * without a leading slash.
 */
import { Button, Input, Select } from "../../ui";
import { useMirrorBackingTypes, useSources } from "../../../hooks/useSources";
import { validateMirrors, type MirrorMapping } from "../../../lib/mirrorConfig";

interface Props {
  value: MirrorMapping[];
  onChange: (next: MirrorMapping[]) => void;
  appType: "immich" | "paperless";
}

export function MirrorsFields({ value, onChange, appType }: Props) {
  const backingTypes = useMirrorBackingTypes();
  const sourcesQ = useSources();
  const backing = (sourcesQ.data ?? []).filter((s) => backingTypes.has(s.id));
  const error = validateMirrors(value, appType, backingTypes);

  const update = (i: number, patch: Partial<MirrorMapping>) =>
    onChange(value.map((m, j) => (j === i ? { ...m, ...patch } : m)));

  return (
    <div className="space-y-3 mt-4">
      <div>
        <h4 className="text-sm font-medium text-fg">Files on disk</h4>
        <p className="text-[11px] text-fg-muted">
          Map this source's files to the copies on disk that back them.
          Linked files are hidden from search by default and counted once
          in storage totals.
        </p>
      </div>
      {value.map((m, i) => {
        const s3 = backingTypes.get(m.source_id) === "s3";
        return (
          <div key={i} className="space-y-2 rounded-md border border-line p-3">
            <Select
              label="Backing source"
              value={m.source_id}
              onChange={(e) => update(i, { source_id: e.target.value })}
              options={[
                { value: "", label: "Select a source…" },
                ...(m.source_id && !backingTypes.has(m.source_id)
                  ? [{ value: m.source_id, label: `${m.source_id} (missing source)` }]
                  : []),
                ...backing.map((s) => ({ value: s.id, label: `${s.name} (${s.type})` })),
              ]}
            />
            {appType === "immich" && (
              <Input
                label="Remote prefix"
                value={m.remote_prefix}
                onChange={(e) => update(i, { remote_prefix: e.target.value })}
                placeholder="/data"
              />
            )}
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
