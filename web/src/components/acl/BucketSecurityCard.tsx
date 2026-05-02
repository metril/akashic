import { memo, useMemo } from "react";

import type { Source } from "../../types";
import { Card } from "../ui";

function PABBadge({ label, blocked }: { label: string; blocked: boolean }) {
  return (
    <div className="flex items-center justify-between p-2 rounded border border-line-subtle">
      <span className="text-xs text-fg">{label}</span>
      <span className={blocked ? "text-emerald-600 text-xs font-medium" : "text-red-600 text-xs font-medium"}>
        {blocked ? "blocked" : "allowed"}
      </span>
    </div>
  );
}

export const BucketSecurityCard = memo(function BucketSecurityCard({
  source,
}: { source: Source }) {
  const meta = source.security_metadata;
  // v0.4.7: pre-stringify the bucket policy. The previous inline
  // JSON.stringify ran on every render — and SourceCard parents
  // re-render on every WS scan.state event for an open scan now
  // that v0.4.7 publishes those at heartbeat frequency. For an
  // enterprise S3 bucket with a multi-statement policy, this was
  // visible hover lag on the Sources list.
  const policyStr = useMemo(
    () =>
      meta?.bucket_policy_present && meta.bucket_policy
        ? JSON.stringify(meta.bucket_policy, null, 2)
        : null,
    [meta?.bucket_policy_present, meta?.bucket_policy],
  );
  const capturedAtStr = useMemo(
    () => (meta ? new Date(meta.captured_at).toLocaleString() : ""),
    [meta?.captured_at],
  );

  if (!meta) return null;
  const pab = meta.public_access_block;

  return (
    <Card padding="md" className="mt-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-fg">Bucket security</h3>
        <span className="text-xs text-fg-subtle">captured {capturedAtStr}</span>
      </div>

      {pab && (
        <>
          <h4 className="text-[11px] uppercase tracking-wider text-fg-subtle mb-2">Public access block</h4>
          <div className="grid grid-cols-2 gap-2 mb-4">
            <PABBadge label="Block public ACLs" blocked={pab.block_public_acls} />
            <PABBadge label="Ignore public ACLs" blocked={pab.ignore_public_acls} />
            <PABBadge label="Block public policy" blocked={pab.block_public_policy} />
            <PABBadge label="Restrict public buckets" blocked={pab.restrict_public_buckets} />
          </div>
        </>
      )}

      {policyStr && (
        <>
          <h4 className="text-[11px] uppercase tracking-wider text-fg-subtle mb-2">Bucket policy</h4>
          <pre className="text-xs bg-app p-3 rounded border border-line-subtle overflow-x-auto">
            {policyStr}
          </pre>
        </>
      )}
    </Card>
  );
});
