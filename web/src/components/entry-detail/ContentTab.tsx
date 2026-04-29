import { useState } from "react";
import type { EntryDetail } from "../../types";
import { Button, Spinner } from "../ui";
import { useEntryPreview } from "../../hooks/useEntryPreview";
import { downloadEntryContent } from "../../lib/downloadEntry";
import { formatBytes } from "../../lib/format";

interface Props {
  entry: EntryDetail;
}

const TEXT_PREVIEW_MIMES = [
  "text/",
  "application/json",
  "application/xml",
  "application/yaml",
  "application/x-yaml",
  "application/javascript",
  "application/x-shellscript",
];

function isPreviewableText(mimeType: string | null): boolean {
  if (!mimeType) return false;
  return TEXT_PREVIEW_MIMES.some((prefix) =>
    prefix.endsWith("/") ? mimeType.startsWith(prefix) : mimeType === prefix,
  );
}

export function ContentTab({ entry }: Props) {
  const isPreviewable = isPreviewableText(entry.mime_type);
  const preview = useEntryPreview(entry.id, isPreviewable);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  async function handleDownload() {
    setDownloading(true);
    setDownloadError(null);
    try {
      await downloadEntryContent(entry.id, entry.name);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  if (entry.kind !== "file") return null;

  return (
    <div className="space-y-3">
      {isPreviewable ? (
        preview.isLoading ? (
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Spinner /> Loading preview…
          </div>
        ) : preview.isError ? (
          <p className="text-xs text-rose-600">
            {preview.error instanceof Error
              ? preview.error.message
              : "Failed to load preview"}
          </p>
        ) : preview.data?.binary ? (
          <p className="text-xs text-gray-500">
            File reads as binary; preview unavailable.
          </p>
        ) : (
          <>
            <pre className="text-xs bg-gray-50 border border-gray-200 rounded-md p-3 max-h-80 overflow-auto whitespace-pre-wrap break-words font-mono text-gray-800">
              {preview.data?.text ?? ""}
            </pre>
            <div className="flex items-center justify-between text-[11px] text-gray-500">
              <span>
                {preview.data?.encoding ?? "unknown"} ·{" "}
                {formatBytes(preview.data?.byte_size_total ?? 0)}
              </span>
              {preview.data?.truncated && (
                <span className="text-amber-700">
                  Truncated — first 64 KB shown
                </span>
              )}
            </div>
          </>
        )
      ) : (
        <p className="text-xs text-gray-500">
          No inline preview for{" "}
          <code className="font-mono">{entry.mime_type ?? "this type"}</code>.
          Use Download to fetch the full file.
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          loading={downloading}
          onClick={handleDownload}
        >
          Download
        </Button>
        {downloadError && (
          <span className="text-xs text-rose-600">{downloadError}</span>
        )}
      </div>
    </div>
  );
}
