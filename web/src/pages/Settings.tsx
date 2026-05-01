import { Link } from "react-router-dom";
import { Card, Page } from "../components/ui";

interface Tile {
  to: string;
  label: string;
  description: string;
}

const tiles: Tile[] = [
  {
    to: "/settings/identities",
    label: "Identities",
    description:
      "Cross-source identity sets and per-source bindings. Used for ACL-aware search and group resolution.",
  },
  {
    to: "/settings/tags",
    label: "Tags",
    description:
      "Custom labels applied to entries for filter and search.",
  },
  {
    to: "/settings/schedules",
    label: "Schedules",
    description:
      "Source scan cadences. One row per source with editable cron strings.",
  },
  {
    to: "/settings/scanners",
    label: "Scanners",
    description:
      "Registered scanner agents. Mint keypairs, set pools, see online status. Scans queue here and a scanner picks them up.",
  },
];

export default function Settings() {
  return (
    <Page
      title="Settings"
      description="Configure how Akashic behaves across sources."
      width="default"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {tiles.map((tile) => (
          <Link
            key={tile.label}
            to={tile.to}
            className="block rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-1"
          >
            <Card
              padding="md"
              className="h-full hover:shadow-md hover:border-accent-200 transition-[box-shadow,border-color] active:scale-[0.99]"
            >
              <h3 className="text-base font-semibold text-fg mb-2">
                {tile.label}
              </h3>
              <p className="text-sm text-fg-muted">{tile.description}</p>
            </Card>
          </Link>
        ))}
      </div>
    </Page>
  );
}
