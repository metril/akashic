import { useEffect, useState } from "react";
import {
  Card,
  SectionState,
  Input,
  Button,
  Badge,
  Page,
} from "../components/ui";
import { useSources, useUpdateSource } from "../hooks/useSources";
import type { Source } from "../types";
import { formatDate } from "../lib/format";

interface RowProps {
  source: Source;
}

function ScheduleRow({ source }: RowProps) {
  const updateSource = useUpdateSource();
  const [draft, setDraft] = useState(source.scan_schedule ?? "");
  const [saved, setSaved] = useState(false);

  // Reset the draft when the source's schedule changes externally
  // (e.g., another user edited it).
  useEffect(() => {
    setDraft(source.scan_schedule ?? "");
  }, [source.scan_schedule]);

  // Auto-clear the 'Saved' pill 2.5s after it appears. The cleanup return
  // cancels the timer if the row unmounts (or saved flips back to false)
  // before the timeout fires.
  useEffect(() => {
    if (!saved) return;
    const id = setTimeout(() => setSaved(false), 2500);
    return () => clearTimeout(id);
  }, [saved]);

  const dirty = (source.scan_schedule ?? "") !== draft.trim();

  async function handleSave() {
    setSaved(false);
    updateSource.reset();
    try {
      await updateSource.mutateAsync({
        id: source.id,
        data: { scan_schedule: draft.trim() || null },
      });
      setSaved(true);
    } catch {
      // Error rendered inline below via updateSource.isError; nothing
      // else to do here.
    }
  }

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-fg truncate">
            {source.name}
          </span>
          <Badge variant="neutral">{source.type}</Badge>
        </div>
        <p className="text-xs text-fg-muted mt-0.5">
          Last scan: {formatDate(source.last_scan_at)}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="*/15 * * * *"
          containerClassName="w-48"
          aria-label={`Schedule for ${source.name}`}
        />
        <Button
          size="sm"
          variant="secondary"
          onClick={handleSave}
          // Disable while in-flight too (review notable) so a second
          // click before the response arrives doesn't double-submit.
          disabled={!dirty || updateSource.isPending}
          loading={
            updateSource.isPending && updateSource.variables?.id === source.id
          }
        >
          Save
        </Button>
        {saved && <span className="text-xs text-emerald-700">Saved</span>}
        {updateSource.isError && updateSource.variables?.id === source.id && (
          <span className="text-xs text-rose-600 max-w-[12rem] truncate" title={
            updateSource.error instanceof Error ? updateSource.error.message : "Failed"
          }>
            {updateSource.error instanceof Error
              ? updateSource.error.message
              : "Failed to save"}
          </span>
        )}
      </div>
    </li>
  );
}

export default function SettingsSchedules() {
  const sourcesQ = useSources();
  const sources = sourcesQ.data ?? [];

  return (
    <Page
      title="Schedules"
      description="Cron expressions that drive automatic scans for each source. Leave blank to disable scheduled scans — manual scans still work from the Sources page."
      width="default"
    >
      <p className="mb-3 text-xs text-fg-muted">
        Cron format: <code>m h dom mon dow</code> — e.g.{" "}
        <code>0 3 * * *</code> runs at 03:00 every day. Leave the input
        blank to disable scheduled scans.
      </p>

      <SectionState
        loading={sourcesQ.isLoading}
        error={sourcesQ.isError ? sourcesQ.error : undefined}
        empty={sources.length === 0}
        emptyTitle="No sources yet"
        emptyMessage="Add a source first; schedules attach to existing sources."
      >
        <Card padding="none">
          <ul className="divide-y divide-line-subtle">
            {sources.map((s) => (
              <ScheduleRow key={s.id} source={s} />
            ))}
          </ul>
        </Card>
      </SectionState>
    </Page>
  );
}
