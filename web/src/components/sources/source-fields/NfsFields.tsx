import { useState } from "react";
import { Input } from "../../ui";
import type { FieldsProps, NfsConfig } from "../sourceTypes";

export function NfsFields({ value, onChange }: FieldsProps<NfsConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  return (
    <div className="space-y-3">
      <Input
        label="Host"
        value={value.host ?? ""}
        onChange={(e) => onChange({ ...value, host: e.target.value })}
        placeholder="nfs.example.com"
        required
      />
      <Input
        label="Export path"
        value={value.export_path ?? ""}
        onChange={(e) => onChange({ ...value, export_path: e.target.value })}
        placeholder="/srv/nfs/data"
        required
      />
      <button
        type="button"
        onClick={() => setShowAdvanced((s) => !s)}
        className="text-xs text-gray-500 hover:text-gray-700 underline"
      >
        {showAdvanced ? "Hide" : "Show"} advanced options
      </button>
      {showAdvanced && (
        <Input
          label="Mount options"
          value={value.mount_options ?? ""}
          onChange={(e) => onChange({ ...value, mount_options: e.target.value })}
          placeholder="vers=4.1,sec=sys"
        />
      )}
      <p className="text-xs text-gray-600 bg-gray-50 rounded-md p-2">
        Test probes TCP reachability of the NFS port (default 2049). Export
        path validity isn't verified — that's checked when the scan mounts.
      </p>
    </div>
  );
}
