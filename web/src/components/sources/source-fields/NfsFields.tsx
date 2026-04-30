import { useEffect, useRef, useState } from "react";
import { Input } from "../../ui";
import type { FieldsProps, NfsConfig } from "../sourceTypes";

export function NfsFields({ value, onChange }: FieldsProps<NfsConfig>) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Aux GIDs use a local string state so the user can type a trailing
  // comma without the parsed-and-rejoined display snapping back over
  // their cursor. We sync to the parent (number[]) on blur, which is
  // the natural commit point for this kind of free-form list input.
  const externalAuxString = (value.auth_aux_gids ?? []).join(", ");
  const [auxText, setAuxText] = useState(externalAuxString);
  const lastSyncedExternal = useRef(externalAuxString);
  useEffect(() => {
    // Re-seed local state ONLY when the external value actually changes
    // (e.g., the parent reloaded a saved source). Don't fight the user
    // mid-typing.
    if (externalAuxString !== lastSyncedExternal.current) {
      setAuxText(externalAuxString);
      lastSyncedExternal.current = externalAuxString;
    }
  }, [externalAuxString]);

  function commitAuxText(s: string) {
    const parsed = s
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p !== "")
      .map((p) => Number(p))
      .filter((n) => Number.isFinite(n) && n >= 0);
    onChange({ ...value, auth_aux_gids: parsed });
    lastSyncedExternal.current = parsed.join(", ");
  }

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
        <div className="space-y-3 rounded-md border border-gray-200 p-3">
          <Input
            label="Port"
            type="number"
            value={value.port?.toString() ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChange({ ...value, port: undefined });
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 1 && n <= 65535) {
                onChange({ ...value, port: n });
              }
            }}
            placeholder="2049"
          />
          <Input
            label="Mount options"
            value={value.mount_options ?? ""}
            onChange={(e) => onChange({ ...value, mount_options: e.target.value })}
            placeholder="vers=4.1,sec=sys"
          />

          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Auth UID"
              type="number"
              value={(value.auth_uid ?? 0).toString()}
              onChange={(e) => {
                const n = Number(e.target.value);
                onChange({ ...value, auth_uid: Number.isFinite(n) && n >= 0 ? n : 0 });
              }}
              placeholder="0"
            />
            <Input
              label="Auth GID"
              type="number"
              value={(value.auth_gid ?? 0).toString()}
              onChange={(e) => {
                const n = Number(e.target.value);
                onChange({ ...value, auth_gid: Number.isFinite(n) && n >= 0 ? n : 0 });
              }}
              placeholder="0"
            />
          </div>
          <p className="text-[11px] text-gray-500 -mt-1">
            UID/GID are presented as the AUTH_SYS identity to the NFS server.
            For exports configured with <code>root_squash</code> (the Linux
            default), use a non-root UID that has access to the share.
          </p>

          <Input
            label="Aux GIDs"
            value={auxText}
            onChange={(e) => setAuxText(e.target.value)}
            onBlur={() => commitAuxText(auxText)}
            placeholder="27, 100"
          />
          <p className="text-[11px] text-gray-500 -mt-1">
            Comma-separated supplementary group IDs (max 16). Useful when the
            export is restricted to a particular group.
          </p>

          <Input
            label="Probe timeout (seconds)"
            type="number"
            value={value.probe_timeout_seconds?.toString() ?? ""}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChange({ ...value, probe_timeout_seconds: undefined });
                return;
              }
              const n = Number(raw);
              if (Number.isFinite(n) && n >= 1 && n <= 60) {
                onChange({ ...value, probe_timeout_seconds: n });
              }
              // Out-of-range / NaN: drop the keystroke; the field
              // shows the previously-valid value. Inline error UI is
              // a future improvement; for now the constraint is also
              // server-validated.
            }}
            placeholder="5"
          />
          <p className="text-[11px] text-gray-500 -mt-1">
            Per-RPC timeout, 1–60 seconds (default 5). Raise this when the
            server is across a slow or congested link.
          </p>
        </div>
      )}
      <p className="text-xs text-gray-600 bg-gray-50 rounded-md p-2">
        Test probes the export end-to-end via MOUNT3 / NFSv4 with AUTH_SYS.
        The success indicator below tells you which protocol path validated
        the mount; a "tcp" tier means the server is reachable but neither
        v3 nor v4 could be fully validated.
      </p>
    </div>
  );
}
