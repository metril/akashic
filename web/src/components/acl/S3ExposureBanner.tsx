import type { Source } from "../../types";

interface Props {
  source: Source | undefined;
}

type State = "public" | "restricted" | "mixed";

function classify(source: Source): State | null {
  const meta = source.security_metadata;
  if (!meta) return null;
  if (meta.is_public_inferred) return "public";
  const pab = meta.public_access_block;
  if (pab && pab.block_public_acls && pab.ignore_public_acls && pab.block_public_policy && pab.restrict_public_buckets) {
    return "restricted";
  }
  return "mixed";
}

const STYLES: Record<State, { wrap: string; label: string; icon: string }> = {
  public:     { wrap: "bg-red-50 border-red-200 text-red-800",         label: "Bucket is publicly accessible.", icon: "⚠" },
  restricted: { wrap: "bg-emerald-50 border-emerald-200 text-emerald-800", label: "Bucket public access blocked.",  icon: "✓" },
  mixed:      { wrap: "bg-amber-50 border-amber-200 text-amber-800",   label: "Bucket exposure: review configuration.", icon: "ℹ" },
};

export function S3ExposureBanner({ source }: Props) {
  if (!source || source.type !== "s3") return null;
  const state = classify(source);
  if (!state) return null;
  const s = STYLES[state];
  return (
    <div className={`mx-6 mt-4 px-4 py-3 rounded border ${s.wrap} flex items-center gap-3`}>
      <span aria-hidden="true">{s.icon}</span>
      <span className="text-sm font-medium flex-1">{s.label}</span>
    </div>
  );
}
