# Workflows view: consume show-your-work index and rasters (amend Steps 44-46)

Proposed amendment to the dev-observatory master plan's workflows view, Steps 44, 45 and 46. This
text changes no dev-observatory code and does not edit its plan; the amendment goes through that
project's own planning pass. Everything below describes `show-your-work`, whose schemas and
behavior are documented in its README and in its `docs/plans/master_plan.md` (sections 3.3, 3.4
and 4).

## What exists now

`show-your-work` is a standalone CLI that owns a per-project `docs/workflows/` folder standard:

- **One header parser** with five required fields: title, `Summary:`, `Sources:`, `Not shown:` and
  `Alt:` (at most 240 characters, ASCII, no control characters, must not repeat the title). Every
  label line ends the `Sources:` block, so an `Alt:` placed after the sources is never swallowed as
  a source; an unknown label is a hard error.
- **`show-your-work check`** validates the folder: header contract, ASCII, absolute-path
  rejection, the id grammar `^[a-z0-9][a-z0-9-]{0,63}$` (rejected, never sanitized), case-folded
  duplicate ids and the legibility budget; with `--render` it also counts nodes and edges after a
  known-garbage render anchor must raise and a known-good one must render.
- **`show-your-work render`** regenerates the folder's `README.md` and a `workflows.json` index
  (schema `show-your-work.workflows.v1`); `render --check` exits 1 when either is stale.
- **`show-your-work render --png`** writes `rasters/<id>-light.png` and `rasters/<id>-dark.png`
  through pinned `mermaidx==0.9.5` with frozen parameters (front-matter `theme` plus
  `flowchart.wrappingWidth: 400`, `scale = 2`, opaque backgrounds `#ffffff` light and `#333333`
  dark), records each raster's `path`, `sha256`, `width`, `height` and `bytes` in the index, writes
  the rasters before the index, and deletes orphan PNGs last.

Once a project's `docs/workflows/` folder is migrated to this standard, the prototype scripts
`docs/workflows/check_diagrams.py` and `docs/workflows/render_readme.py` are deleted and the tool
regenerates the README and index.

## Proposed amendment

### Step 44: satisfied by migration

Step 44 requires an `Alt:` line on every diagram and parser support for it. After the migration,
`Alt:` is required by the one parser: `show-your-work check` fails any diagram without it
(`header-missing-alt`), the generated README renders the alt text as its own `**Alt:**` field and
never as a source bullet, and the scripts Step 44 planned to extend no longer exist. Proposed
status: **done by migration**, with the migration commit as evidence. Its verification commands
become `show-your-work check docs/workflows` and `show-your-work render docs/workflows --check`.

### Steps 45 and 46: consume `workflows.json` and `rasters/`

Rendering, header parsing, id validation, raster hashing and orphan cleanup now happen upstream.
Proposed rewrite: Step 45 no longer builds a renderer; the rasters come from
`show-your-work render docs/workflows --png`. Step 46 becomes a mapping step that reads
`docs/workflows/workflows.json` and its `rasters/` folder, verifies each raster against the index,
and builds the view's own artifact from it. The viewer then needs no renderer dependency of its
own for this feature.

Field-by-field, what the mapping step reads from `show-your-work.workflows.v1`:

| Index field | Meaning | How the viewer should use it |
|---|---|---|
| `schema` | always `show-your-work.workflows.v1` | refuse any other value |
| `generator` | `show-your-work <version>` | provenance only |
| `generated_at` | ISO-8601, UTC offset exactly zero | when the index was rendered; the viewer can keep its own freshness stamp |
| `source_dir` | the folder, relative to the nearest `.git` ancestor, POSIX separators (`"."` when none) | provenance only |
| `items[]` | one per diagram, sorted by `id`, ids unique, exact key set | re-assert sort, uniqueness and the key set |
| `items[].id` | the `.mmd` stem, matching `^[a-z0-9][a-z0-9-]{0,63}$` | item id; re-check the grammar defensively |
| `items[].label` | the header title verbatim (<= 120 chars) | display label |
| `items[].summary` | the `Summary:` field (<= 160 chars) | display summary |
| `items[].alt` | the `Alt:` field (<= 240 ASCII chars) | the image's text alternative |
| `items[].not_shown` | the `Not shown:` field (<= 400 chars) | optional display; may be omitted |
| `items[].sources` | list of repo-relative paths or URLs | optional display; never opened |
| `items[].source_path` | `<source_dir>/<id>.mmd` | display-only provenance; never opened |
| `items[].source_sha256` | SHA-256 of the source after CRLF to LF normalization | optional staleness signal |
| `items[].engine` | `"mermaid"`, or `"excalidraw"` when an `<id>.excalidraw.svg` sibling exists | if the viewer supports only `mermaid`, refuse the whole run on any other value rather than silently dropping the item |
| `items[].rasters` | `null`, or `{"light": {...}, "dark": {...}}` | `null` on any item means `render --png` has not run (or the rasters no longer match): refuse the run |
| `rasters.<theme>.path` | `rasters/<id>-<theme>.png`, relative to the workflows folder | locate the file |
| `rasters.<theme>.sha256` | SHA-256 of the PNG bytes | re-verify against the bytes on disk |
| `rasters.<theme>.width` / `height` | from the PNG header, `2 x round_half_up(viewBox size)` | re-verify; apply the viewer's own dimension caps (upstream caps are 16,384 px per side and 50,000,000 px in total, which may be looser than the viewer's) |
| `rasters.<theme>.bytes` | file size | re-verify; apply the viewer's own size cap |

Recommendations for the mapping step:

1. **Refuse, never partially publish.** A `null` raster entry, a missing file or a hash, size or
   dimension mismatch aborts the run with no output written.
2. **Media type.** The index carries no media type; upstream writes PNG only, so verify the PNG
   signature and record the media type as a constant.
3. **Asset location.** If the viewer requires its assets inside its own project directory, copy
   each verified raster there (rasters first, the viewer's artifact last, each via a temporary file
   and an atomic replace) rather than pointing at the upstream folder.
4. **Freshness.** `show-your-work render docs/workflows --check` exits 1 when the README or index
   is stale; run it first, or rely on re-verifying every raster hash.
5. **Raster hashes across machines.** Raster bytes are not guaranteed identical across machines or
   renderer versions. The pinned mermaidx 0.9.5 was observed to rasterize with system fonts
   skipped and its own bundled font, which may make them stable in practice; that is unverified,
   so per-request hash verification should stay.

## Stale references

These three scripts are superseded:

- `docs/workflows/check_diagrams.py` (deleted by the migration)
- `docs/workflows/render_readme.py` (deleted by the migration)
- `scripts/render_workflows.py` (planned, superseded by `show-your-work render --png` plus the
  mapping step)

Every reference to these in the Observatory plan is stale, including the commands, file lists and
done-when checks that name them.
