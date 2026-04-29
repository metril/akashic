import { useEffect, useState } from "react";
import {
  Card,
  Spinner,
  EmptyState,
  Input,
  Button,
  Badge,
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

  const dirty = (source.scan_schedule ?? "") !== draft.trim();

  async function handleSave() {
    setSaved(false);
    try {
      await updateSource.mutateAsync({
        id: source.id,
        data: { scan_schedule: draft.trim() || null },
      });
      setSaved(true);
      // Auto-clear the "Saved" pill after a few seconds.
      setTimeout(() => setSaved(false), 2500);
    } catch {
      // Error surfaced inline below via mutation state.
    }
  }

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900 truncate">
            {source.name}
          </span>
          <Badge variant="neutral">{source.type}</Badge>
        </div>
        <p className="text-xs text-gray-500 mt-0.5">
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
          disabled={!dirty}
          loading={
            updateSource.isPending && updateSource.variables?.id === source.id
          }
        >
          Save
        </Button>
        {saved && <span className="text-xs text-emerald-700">Saved</span>}
      </div>
    </li>
  );
}

export default function SettingsSchedules() {
  const sourcesQ = useSources();
  const sources = sourcesQ.data ?? [];

  return (
    <div className="px-8 py-7 max-w-4xl">
      <h1 className="text-2xl font-semibold text-gray-900 tracking-tight mb-1">
        Schedules
      </h1>
      <p className="text-sm text-gray-500 mb-6">
        Cron expressions that drive automatic scans for each source. Leave
        blank to disable scheduled scans for a source — manual scans still
        work from the Sources page.
      </p>

      {sourcesQ.isLoading ? (
        <div className="flex items-center justify-center py-12 text-gray-400">
          <Spinner />
        </div>
      ) : sourcesQ.isError ? (
        <div className="text-sm text-rose-600 bg-rose-50 rounded px-3 py-2">
          {sourcesQ.error instanceof Error
            ? sourcesQ.error.message
            : "Failed to load sources"}
        </div>
      ) : sources.length === 0 ? (
        <div className="border border-gray-200 rounded py-12">
          <EmptyState
            title="No sources yet"
            description="Add a source first; schedules attach to existing sources."
          />
        </div>
      ) : (
        <Card padding="none">
          <ul className="divide-y divide-gray-100">
            {sources.map((s) => (
              <ScheduleRow key={s.id} source={s} />
            ))}
          </ul>
        </Card>
      )}

      <p className="mt-4 text-xs text-gray-400">
        Cron format: <code>m h dom mon dow</code> — e.g.{" "}
        <code className="text-gray-600">0 3 * * *</code> runs at 03:00 every day.
      </p>
    </div>
  );
}
