import { useState } from "react";
import type { ACL } from "../../types";
import { diffACL, type ACLDiffItem } from "../../lib/aclDiff";

const ICON: Record<ACLDiffItem["kind"], string> = {
  type_changed:  "⇄",
  added:         "+",
  removed:       "−",
  modified:      "~",
  reordered:     "↕",
  owner_changed: "↻",
  group_changed: "↻",
};

const COLOR: Record<ACLDiffItem["kind"], string> = {
  type_changed:  "text-gray-600",
  added:         "text-emerald-700",
  removed:       "text-red-700",
  modified:      "text-amber-700",
  reordered:     "text-violet-700",
  owner_changed: "text-blue-700",
  group_changed: "text-blue-700",
};

function itemText(item: ACLDiffItem): string {
  switch (item.kind) {
    case "type_changed":  return `Type ${item.from} → ${item.to}`;
    case "owner_changed": return `Owner ${item.from} → ${item.to}`;
    case "group_changed": return `Group ${item.from} → ${item.to}`;
    case "added":
    case "removed":
    case "modified":
    case "reordered":
      return item.summary;
  }
}

function DiffRow({ item }: { item: ACLDiffItem }) {
  const scope = "scope" in item && item.scope ? `[${item.scope}] ` : "";
  return (
    <li className={`text-sm ${COLOR[item.kind]} flex items-baseline gap-2`}>
      <span className="font-mono w-3 text-center">{ICON[item.kind]}</span>
      <span>
        {scope}
        {itemText(item)}
      </span>
    </li>
  );
}

export function ACLDiff({ prev, curr }: { prev: ACL | null; curr: ACL | null }) {
  const items = diffACL(prev, curr);
  const [showInherited, setShowInherited] = useState(false);

  if (items.length === 0) return null;

  const direct = items.filter((i) => !("scope" in i && i.scope === "inherited"));
  const inherited = items.filter((i) => "scope" in i && i.scope === "inherited");

  return (
    <div className="mt-1">
      <ul className="space-y-0.5">
        {direct.map((item, i) => (
          <DiffRow key={i} item={item} />
        ))}
      </ul>
      {inherited.length > 0 && (
        <div className="mt-1">
          <button
            type="button"
            onClick={() => setShowInherited((v) => !v)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            {showInherited ? "▾" : "▸"} Inherited changes ({inherited.length})
          </button>
          {showInherited && (
            <ul className="space-y-0.5 mt-1 ml-3">
              {inherited.map((item, i) => (
                <DiffRow key={i} item={item} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
