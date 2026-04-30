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

export function BucketSecurityCard({ source }: { source: Source }) {
  const meta = source.security_metadata;
  if (!meta) return null;
  const pab = meta.public_access_block;

  return (
    <Card padding="md" className="mt-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-fg">Bucket security</h3>
        <span className="text-xs text-fg-subtle">captured {new Date(meta.captured_at).toLocaleString()}</span>
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

      {meta.bucket_policy_present && meta.bucket_policy && (
        <>
          <h4 className="text-[11px] uppercase tracking-wider text-fg-subtle mb-2">Bucket policy</h4>
          <pre className="text-xs bg-app p-3 rounded border border-line-subtle overflow-x-auto">
            {JSON.stringify(meta.bucket_policy, null, 2)}
          </pre>
        </>
      )}
    </Card>
  );
}
