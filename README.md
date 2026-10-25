# show-your-work

**Phase 1 complete** — issues #2–8 closed. `init`, `check`, `render` and `render --png` shipped and dogfooded on this repo's own `docs/workflows/`. 284 tests passing, 0 type errors, 0 lint violations.

## Description

`show-your-work` is a local-first command-line tool that owns one small standard: a
`docs/workflows/` folder inside each project, holding that project's workflow diagrams. It
scaffolds the folder (`init`), validates every diagram against a required header and a
legibility budget (`check`), and regenerates the folder's `README.md` and a `workflows.json`
index from the sources (`render`), optionally with light and dark PNG rasters (`render --png`).

The diagrams are mermaid flowcharts in `.mmd` files. The documents live in the consumer project;
this repository is only the template, the validator and the generator. The `init`, `check` and
`render` verbs use the Python standard library alone; rendering through mermaid needs the
optional, exactly pinned `mermaidx` renderer.

One real consumer is Skill Mesh's
[workflow diagram catalog](https://github.com/aberson/skill-mesh/tree/main/docs/workflows), which
uses this folder contract for 57 diagrams. `show-your-work` is an authoring and validation
dependency for that documentation; installed Skill Mesh skills do not need it at runtime.

## Why

Workflow diagrams drift. Someone draws a picture of how a pipeline works, the pipeline changes,
and nobody notices that the picture is now wrong, or that it silently leaves out the part a reader
most needs. Hand-maintained indexes of those pictures drift too.

`show-your-work` keeps a diagram folder honest by making every diagram declare where it was drawn
from (`Sources:`), what it deliberately leaves out (`Not shown:`) and what it looks like for a
reader who cannot see it (`Alt:`); by refusing diagrams too large to read at a glance; and by
generating the folder's README and index from the sources so they cannot fall out of date
unnoticed (`render --check` exits 1 when they have). GitHub renders the generated README's mermaid
blocks natively, so a project's diagrams are readable before any other tool exists.

## Install

Prerequisites: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), and a checkout of this
repository. There is no package-index release yet; the tool runs from the checkout.

From the repository root:

```powershell
uv sync --extra dev
```

This creates `.venv` with the console script `show-your-work` and the development tools (pytest,
ruff, mypy). The stdlib verbs need nothing else.

To also install the pinned renderer (`mermaidx==0.9.5`, needed only for `check --render` and
`render --png`):

```powershell
uv sync --extra dev --extra render
```

`uv sync` is exact: a later bare `uv sync`, or `uv sync --extra dev` alone, uninstalls the
renderer again. Repeat every extra you want on each sync.

## Quick start

From the repository root, run the three verbs against this repository's own dogfooded
`docs/workflows/` folder:

```powershell
uv run show-your-work init docs/workflows
uv run show-your-work check docs/workflows
uv run show-your-work render docs/workflows
```

`init` creates the folder if it is missing and copies `intro.md` and `example-workflow.mmd` from
the templates only where they are absent (here both already exist, so it reports `kept` for each),
then runs `check` and `render`. `check` prints one row per diagram and a summary line such as
`1/1 pass (budget: <=14 nodes, <=20 edges, node label <=60, edge label <=40, <=2 subgraphs)`.
`render` reports `README.md` and `workflows.json` as `written` or `up to date`.

With the renderer installed, `check` can render every diagram for exact node and edge counts, and
`render` can write rasters:

```powershell
uv run show-your-work check docs/workflows --render
uv run show-your-work render docs/workflows --png
```

In this repository, `render --png` writes untracked `docs/workflows/rasters/*.png` and rewrites the
tracked `docs/workflows/workflows.json` to record them; `git restore docs/workflows/workflows.json`
and deleting `docs/workflows/rasters/` undo it.

`TARGET` can be any directory, including another project's folder, for example (illustrative
path) `uv run show-your-work check path/to/other-project/docs/workflows`. It exits 2
(`target is not a directory`) until that folder exists, for example after `init` created it.

## Verify

From the repository root, after `uv sync --extra dev`:

```powershell
uv run pytest -q
```

Verification passes when the command exits 0 with no failed tests. Tests that need the renderer
skip with a reason unless `uv sync --extra dev --extra render` was used; an optional local-only
test skips when its input is absent; symlink tests skip on Windows
accounts that may not create symlinks. The full gate also runs:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src tests
```

## Supported platforms

Verified on Windows 11 with Python 3.12+ and uv: the full test suite, lint, format and type gates
pass there, with and without the `render` extra. The code is plain Python with no
platform-specific runtime dependency, but Linux and macOS have not been verified. The renderer
extra depends on `mermaidx` 0.9.5 publishing a wheel for the host.

## Input and output

Every verb takes an optional positional `TARGET` directory. Without one it uses `./docs/workflows`
resolved from the current directory and exits 2 if that folder does not exist (`init` creates it
instead). Discovery is top-level only: `TARGET/*.mmd`, never recursive.

| Verb | Synopsis | Reads | Writes |
|---|---|---|---|
| `init` | `show-your-work init [TARGET] [--force]` | templates | `intro.md`, `example-workflow.mmd` where absent, then what `render` writes |
| `check` | `show-your-work check [TARGET] [--render] [--json]` | `*.mmd`, `*.excalidraw.svg` | nothing |
| `render` | `show-your-work render [TARGET] [--check] [--png] [--json]` | `*.mmd`, `intro.md`, the previous `workflows.json` | `README.md`, `workflows.json`; with `--png` also `rasters/<id>-light.png`, `rasters/<id>-dark.png` |

A consumer folder looks like this:

```text
docs/workflows/
  intro.md              hand-authored; embedded verbatim at the top of the generated README
  <id>.mmd              one diagram per file; id = file stem
  <id>.excalidraw.svg   optional hand-edited lane for an id (see "Excalidraw lane" below)
  README.md             GENERATED by render; never hand-edited
  workflows.json        GENERATED index (schema show-your-work.workflows.v1)
  rasters/              optional, GENERATED by render --png
```

- **id** = the `.mmd` file stem and must match `^[a-z0-9][a-z0-9-]{0,63}$`. A non-matching stem is
  rejected, never sanitized; case-variant duplicates are rejected. Renaming a file is a new id.
- `init` never overwrites. `--force` re-copies only template-origin files, recognised by a marker
  on their last line (`%% show-your-work:template` in a `.mmd`, `<!-- show-your-work:template -->`
  in `intro.md`); delete the marker once a file is yours.
- `render` runs `check` first and refuses (exit 1, nothing written) when any file fails.
  `render --check` writes nothing and exits 1 when `README.md` or `workflows.json` is stale.
- Given identical sources, `render` produces a byte-identical `README.md` and an identical
  `workflows.json` except `generated_at`. CRLF and LF sources produce the same output.
- Human output is a fixed-width table plus the summary line; `--json` prints a
  `show-your-work.report.v1` document instead (see "Schemas").

### Header contract

Every `.mmd` opens with a block of mermaid `%%` comment lines. The first line is the title,
verbatim. Then come label lines of the form `Label: value`. All five fields are required:

| Field | Bound | Notes |
|---|---|---|
| title | <= 120 chars | the first `%%` line, verbatim, even if it looks like a label |
| `Summary:` | <= 160 chars | one sentence, shown in the generated contents list |
| `Sources:` | >= 1 entry | repo-relative paths or URLs, on the label line and/or `%%   - path` bullets; a drive-letter absolute path is rejected (`abs-path`) |
| `Not shown:` | <= 400 chars | the omissions the diagram declares |
| `Alt:` | <= 240 chars, ASCII, no control characters | describes the picture; must not repeat the title (`alt-repeats-title`) |

Parsing rules:

- A label line matches `^([A-Za-z][A-Za-z ]{0,30}):(\s|$)` on the text after `%%`; label names
  compare case-insensitively. The required space after the colon means an unbulleted `https://...`
  source is never mistaken for a label.
- Any label other than the four above is a hard error (`header-unknown-label`) naming it.
- A bullet (`- ...`) always continues `Sources:`. A non-label line directly inside the `Sources:`
  block is also a source. Every label line ends the `Sources:` block, so an `Alt:` after the
  sources is never swallowed as a source.
- Any other non-label header line is a hard error (`header-stray-line`).
- The header ends at the first line that is neither `%%` nor blank.
- The whole file must be ASCII and must not contain a machine-absolute `Users` path. CRLF and LF
  line endings are both accepted.

A valid header:

```text
%% Release Checklist
%% Summary: The four gates a release passes in order - tests, changelog, tag, publish - and the one shortcut that is forbidden.
%% Sources:
%%   - docs/release.md
%%   - scripts/release.py
%% Not shown: hotfix path, rollback, signing keys
%% Alt: Four boxes in a horizontal row joined by right-pointing arrows, with one red crossed-out arrow skipping from the first box straight to the last.

flowchart TD
    ...
```

### Rasters (`render --png`) and the host-font caveat

`render --png` renders every diagram twice through mermaidx, with a mermaid front-matter config
injected at render time only (the committed sources stay config-free): theme `default` on an
opaque `#ffffff` background, and theme `dark` on an opaque `#333333` background (mermaid's own
dark-theme background), both with `flowchart.wrappingWidth: 400` and `scale = 2`. These values are
frozen constants because changing any of them changes every raster hash and dimension. A PNG's
width and height are `2 x round_half_up(viewBox size)`. All rasters are rendered in memory first; then the PNGs are written, then
`README.md` and `workflows.json`, and last any orphan `*.png` directly inside `rasters/` that the
new index no longer references is deleted (never recursing, never following a symlink, never
touching another file type).

Run `--render` and `--png` only on diagrams you trust: the renderer processes them with a bundled
third-party engine. Work is bounded: a source larger than 256 KiB makes the gate exit 2 without
reading it, and a canvas over 16,384 px per side or 50,000,000 px in total exits 2 before
rasterizing.

**Host-font caveat.** Raster bytes, and therefore the hashes in `workflows.json`, are not
guaranteed to be identical across machines or renderer versions. The plan treated them as
host-font dependent because the rasterizer (resvg) draws the text. In this build, the pinned
mermaidx 0.9.5 was observed to rasterize with system fonts skipped and its own bundled font, which
may make the bytes stable across machines in practice; that has not been verified on a second
machine. For that reason `render --check` ignores raster hashes unless `--png` is passed, and a
raster mismatch is fixed by re-running `render --png`, not by editing the index.

### Excalidraw lane

The lane is documented, not automated. To hand-edit a diagram beyond what mermaid lays out:
author it in mermaid first, convert it once by hand at excalidraw.com (Insert, then Mermaid), save
the result as `<id>.excalidraw.svg` beside the `.mmd`, and edit it further in the Excalidraw
VS Code extension. The `.mmd` stays as the header and provenance record. The tool's only
involvement: `check` fails an `.excalidraw.svg` with no matching `.mmd` (`excalidraw-orphan`), and
`render` sets that item's `engine` to `"excalidraw"`. The tool does not convert or rasterize the
SVG; `render --png` still rasterizes the `.mmd`.

## Legibility budget (thresholds)

`check` enforces a budget so every diagram stays readable at a glance. Split a bigger workflow
into several diagrams, or leave detail out and declare it under `Not shown:`.

| Limit | Value | Finding code |
|---|---|---|
| nodes | <= 14 | `budget-nodes` |
| edges | <= 20 | `budget-edges` |
| longest node label | <= 60 chars | `budget-node-label` |
| longest edge label | <= 40 chars | `budget-edge-label` |
| subgraphs | <= 2, and every `subgraph` closed by `end` | `budget-subgraphs`, `subgraph-unbalanced` |

Label lengths and subgraphs are measured from the source. Node and edge counts are exact only
under `check --render`, which renders each diagram through mermaid and counts the SVG; without it
the table shows `-` for both and every run prints a `NOTE:` line saying the node and edge budget
was not checked. It is never a silent pass.

Before rendering any file, every render path (`check --render`, `render --png`) renders a
known-garbage diagram that must raise and a known-good diagram that must succeed. If either
anchor misbehaves, the run exits 2 before touching a file: a renderer that accepts garbage, or
rejects everything, cannot produce a trustworthy result.

The full finding-code list is `header-missing-title`, `header-missing-summary`,
`header-missing-sources`, `header-missing-not-shown`, `header-missing-alt`, `header-bound`,
`header-unknown-label`, `header-stray-line`, `alt-repeats-title`, `alt-not-ascii`, `abs-path`,
`non-ascii`, `id-grammar`, `id-duplicate`, `excalidraw-orphan`, `budget-nodes`, `budget-edges`,
`budget-node-label`, `budget-edge-label`, `budget-subgraphs`, `subgraph-unbalanced` and
`render-error`. Codes are stable strings.

## Exit codes

| Code | Meaning (all verbs) |
|---|---|
| 0 | success: all files pass, outputs written or up to date |
| 1 | at least one file failed `check` (so `render` wrote nothing), or `render --check` found stale output |
| 2 | the gate could not run: no `*.mmd` files, no default `docs/workflows`, `TARGET` unusable or unreadable, the renderer missing or broken when `--render` / `--png` needs it, a render anchor broken, or an output that could not be written |
| 64 | usage error: no verb, unknown verb, unknown flag, missing value |

argparse's own usage-error code is 2, which would collide with "gate could not run", so usage
errors are remapped to 64. Gate messages go to stderr; with `--json` a report carrying
`exit_code` still goes to stdout.

## Schemas

### `workflows.json` (`show-your-work.workflows.v1`)

```json
{
  "schema": "show-your-work.workflows.v1",
  "generator": "show-your-work 0.1.0",
  "generated_at": "2026-10-24T18:00:00+00:00",
  "source_dir": "docs/workflows",
  "items": [
    {
      "id": "release-checklist",
      "label": "Release Checklist",
      "summary": "The four gates a release passes in order ...",
      "alt": "Four boxes in a row joined by arrows ...",
      "not_shown": "hotfix path, rollback ...",
      "sources": ["docs/release.md"],
      "source_path": "docs/workflows/release-checklist.mmd",
      "source_sha256": "<64 hex>",
      "engine": "mermaid",
      "rasters": null
    }
  ]
}
```

- `items` are sorted by `id`, with this exact key set per item.
- `label` is the header title verbatim; `summary`, `alt`, `not_shown` and `sources` are the parsed
  header fields.
- `source_dir` is `TARGET` relative to the project root (the nearest ancestor holding a `.git`
  file or directory), with POSIX separators, or `"."` when there is none. `source_path` is
  `<source_dir>/<id>.mmd`. Neither depends on the current directory.
- `source_sha256` is SHA-256 over the source bytes after CRLF-to-LF normalization.
- `engine` is `"mermaid"`, or `"excalidraw"` when `<id>.excalidraw.svg` sits beside the `.mmd`.
- `rasters` is `null` until `render --png` runs, then
  `{"light": {"path": "rasters/<id>-light.png", "sha256": ..., "width": N, "height": N, "bytes": N}, "dark": {...}}`.
  A plain `render` keeps an existing entry only while both files still exist and still match its
  `sha256` and `bytes`, and writes `null` otherwise; it never deletes raster files.
- `generated_at` is ISO-8601 with a UTC offset of exactly zero.

This index is the tool's own format, not any downstream viewer's envelope. A consumer maps from
it; nothing here guesses a consumer's limits or asset paths.

### `--json` report (`show-your-work.report.v1`)

```json
{
  "schema": "show-your-work.report.v1",
  "target": "<absolute TARGET, POSIX separators>",
  "verb": "check",
  "files": [
    {"file": "example-workflow.mmd", "id": "example-workflow", "pass": true,
     "nodes": null, "edges": null, "findings": []}
  ],
  "summary": {"total": 1, "passed": 1, "failed": 0, "rendered": false,
              "budget": {"nodes": 14, "edges": 20, "node_label": 60, "edge_label": 40, "subgraphs": 2}},
  "exit_code": 0
}
```

`files` holds one row per `.mmd` (plus one per orphan `.excalidraw.svg`), sorted by file name;
`nodes` / `edges` are `null` when not rendered; each finding is `{"code", "message"}`.

## Scope

In scope: the `docs/workflows/` folder standard, its templates, the `check` gate and the `render`
generator. Out of scope for v1: any viewer, server, route or static export of the diagrams (a
downstream reader can consume `workflows.json` and `rasters/`); run records or attempt ledgers;
automated excalidraw conversion; a headless-browser fallback renderer; any daemon, database or
network access. The tool never keeps a central list of other projects' diagrams.

## Development

The authoritative design is [`docs/plans/master_plan.md`](docs/plans/master_plan.md) (sections 3,
4 and 8). Layout:

- `src/show_your_work/constants.py` is the one home for every budget number, header bound, regex,
  schema string, finding code, exit code and raster constant; tests assert other modules share
  these objects rather than redefining them.
- `header.py` is the single header parser; `checks.py` the `check` logic; `renderer.py` the
  optional mermaidx wrapper and anchors; `render.py` the README, index and raster writer;
  `scaffold.py` the `init` verb; `cli.py` argparse, exit codes and `--json`.
- `templates/workflows/` holds the files `init` copies (shipped inside the wheel).
- `tests/fixtures/` holds the known-garbage anchor and synthetic `bad-headers`, `over-budget`,
  `good` and `realistic` sets; every fixture is synthetic.
- `docs/workflows/` is this repository's own dogfooded folder.

The gate is the four commands under "Verify". Conformance with the utility-project-standard
(declared in [`utility-project.toml`](utility-project.toml): `standalone-cli`, `public`,
`experimental`) is checked by that project's checker, from a sibling checkout of it:

```powershell
uv run --project ../utility-project-standard utility-standard check .
```

A procedure for migrating an existing diagram folder to this standard is in
[`docs/migration-coding-root.md`](docs/migration-coding-root.md).

## License

MIT. See [`LICENSE`](LICENSE).
