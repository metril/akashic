import { Link } from "react-router-dom";
import { Card } from "../components/ui";

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
];

export default function Settings() {
  return (
    <div className="px-8 py-7 max-w-5xl">
      <div className="mb-7">
        <h1 className="text-2xl font-semibold text-gray-900 tracking-tight">
          Settings
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          Configure how akashic behaves across sources.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {tiles.map((tile) => (
          <Link key={tile.label} to={tile.to} className="block">
            <Card
              padding="md"
              className="h-full hover:shadow-md transition-shadow"
            >
              <h3 className="text-base font-semibold text-gray-900 mb-2">
                {tile.label}
              </h3>
              <p className="text-sm text-gray-500">{tile.description}</p>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
