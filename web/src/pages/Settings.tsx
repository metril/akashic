import { Link } from "react-router-dom";
import { Card, Badge } from "../components/ui";

interface Tile {
  to: string | null;
  label: string;
  description: string;
  comingSoon?: boolean;
}

const tiles: Tile[] = [
  {
    to: "/settings/identities",
    label: "Identities",
    description:
      "Cross-source identity sets and per-source bindings. Used for ACL-aware search and group resolution.",
  },
  {
    to: null,
    label: "Tags",
    description:
      "Custom labels applied to entries for filter and search.",
    comingSoon: true,
  },
  {
    to: null,
    label: "Schedules",
    description:
      "Source scan cadences and one-off triggers.",
    comingSoon: true,
  },
];

function TileBody({ tile }: { tile: Tile }) {
  return (
    <>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-base font-semibold text-gray-900">{tile.label}</h3>
        {tile.comingSoon && <Badge variant="neutral">Coming soon</Badge>}
      </div>
      <p className="text-sm text-gray-500">{tile.description}</p>
    </>
  );
}

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
        {tiles.map((tile) =>
          tile.to ? (
            <Link key={tile.label} to={tile.to} className="block">
              <Card
                padding="md"
                className="h-full hover:shadow-md transition-shadow"
              >
                <TileBody tile={tile} />
              </Card>
            </Link>
          ) : (
            <Card
              key={tile.label}
              padding="md"
              className="h-full opacity-60 cursor-not-allowed"
            >
              <TileBody tile={tile} />
            </Card>
          ),
        )}
      </div>
    </div>
  );
}
