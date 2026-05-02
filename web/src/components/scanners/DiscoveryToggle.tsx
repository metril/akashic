/**
 * Admin-only switch that flips the runtime `discovery_enabled`
 * server setting. Reflects the api's current state — toggling here
 * PATCHes the api which fans out a pubsub event so other api workers
 * bust their cache within a few hundred ms.
 */
import { Spinner } from "../ui";
import { useServerSetting } from "../../hooks/useServerSetting";

export function DiscoveryToggle() {
  const { value: enabled, query, set } = useServerSetting<boolean>(
    "discovery_enabled", false,
  );

  return (
    <label className="inline-flex items-center gap-2 cursor-pointer text-xs">
      <span className="text-fg-muted">Discovery:</span>
      {query.isLoading || set.isPending ? (
        <Spinner />
      ) : (
        <>
          <input
            type="checkbox"
            className="sr-only peer"
            checked={enabled}
            onChange={(e) => set.mutate(e.target.checked)}
          />
          <span
            aria-hidden
            className={`relative inline-block w-9 h-5 rounded-full transition-colors ${
              enabled ? "bg-emerald-500" : "bg-fg-subtle/40"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                enabled ? "translate-x-4" : ""
              }`}
            />
          </span>
          <span className="text-fg font-medium">
            {enabled ? "on" : "off"}
          </span>
        </>
      )}
    </label>
  );
}
