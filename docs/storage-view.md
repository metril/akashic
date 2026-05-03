# Storage view

The Storage tab visualises how a source is laid out on disk. Two
layouts share the same data and the same interactions:

- **Treemap** — rectangular nesting (default).
- **Sunburst** — radial nesting.

Both render every file as its own coloured cell, sized by bytes.
Clicking a directory drills into it; the view animates between
states.

## Layouts

| Layout | Strengths | Renderer |
|--------|-----------|----------|
| Treemap | Spatial scanning of large file counts; detail at depth | WebGL2, single instanced draw call |
| Sunburst | "What fraction of this folder is X" at a glance | Canvas2D with cached `Path2D` per arc |

Toggle in the top-right next to the colour-mode selector.

## Color modes

| Mode | What the colour encodes |
|------|--------------------------|
| Type | File extension family — similar extensions get similar hues |
| Age | Hot (modified < 90 days) / warm (90–365) / cold (> 365) |
| Owner | Per-owner hue derived from `owner_name` |
| Risk | **Admin only.** Effective ACL — public / authenticated / restricted |

### Risk mode

Risk mode highlights how widely each file is exposed. The colour
maps to the entry's `viewable_by_read` ACL token set:

- 🔴 **Public** — `viewable_by_read` contains the wildcard token
  `*`. Anyone, including unauthenticated users on a permissive
  source, can read this file.
- 🟡 **Authenticated** — `viewable_by_read` contains `auth` (any
  authenticated principal) but not `*`. Anyone with valid creds
  on this Akashic instance can read it.
- 🟢 **Restricted** — neither `*` nor `auth`. Only specific
  users/groups (the listed SIDs/UIDs) can read it.

Risk mode is **admin only** because the colouring telegraphs
which directories hold the most exposed content — useful for
clean-up, sensitive in the wrong hands. The colour-mode toggle
hides the option for non-admins.

The classification is computed from each file's full ACL during
ingest (`viewable_by_read` is a generated column on the Entry
table). For the underlying access-token model, see
[permissions-model.md](permissions-model.md).

## Interactions

| Gesture | What it does |
|---------|--------------|
| Click a directory | Drill in — view animates from current to focused. |
| Click a file | Opens the entry detail drawer. |
| Right-click any cell | Context menu: open in Browse, filter Search to this folder. |
| Wheel up | Treemap: zoom in around the cursor; at max zoom, drill into the directory under the cursor. Sunburst: drill into the hovered arc. |
| Wheel down | Treemap: zoom out around the cursor; at fit (identity viewport), drill up to parent. Sunburst: drill up. |
| Shift + drag | Pan (treemap only). |
| **[Fit]** button | Reset the treemap viewport to fit-to-container. Visible only when zoomed/panned away from identity. |
| **⬆ Up** in toolbar | Drill up by one level. |
| Breadcrumb | Drill back to any ancestor. |

A 350 ms cooldown after every drill prevents a single physical
scroll-wheel motion from cascading through several levels.

## Cross-source view

When a deployment has more than one source and no source is
selected, the Storage view shows every source as its own
treemap/sunburst cell, sized by total bytes per source. Click (or
wheel-drill) into one to enter that source's tree. Single-source
deployments skip this step automatically — the URL silently picks
up `?source=<id>` after the sources query resolves.

## Hover sidebar

The right-hand panel follows the cursor: it shows the chain from
root to the hovered cell plus the cell's size and (when
applicable) the access tokens that landed it in its risk bucket.
Click any segment in the chain to drill there.

## Live mid-scan updates

When [`STREAMING_TOPCHILDREN`](configuration.md#performance) is
enabled, ingest batches mark touched parent paths in a Redis
dirty set; a background worker incrementally rebuilds the
storage explorer's `top_children` rollup so the Storage view sees
fresh data while a scan is still in progress instead of waiting
for the post-scan rollup. Recommended on for any deployment large
enough that the post-scan rollup feels stale.

## Performance notes (for the curious)

The treemap renders thousands of rectangles via a single
WebGL2 instanced draw call; hover does **not** rebuild the
scene (Phase 1 of v0.4.14) and pan-only frames do **not**
re-upload the GPU buffer (Phase 2). The sunburst renders to
Canvas2D using a `Path2D` cached per arc at layout time and
coalesces hover redraws to one per `requestAnimationFrame`
(v0.4.15). At ~5,000 rectangles or arcs the view stays at
60 fps on integrated GPUs.
