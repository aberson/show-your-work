# show-your-work — project instructions

Workflow transparency utility: one centralized standard and CLI that scaffolds, validates and
renders a `docs/workflows/` folder inside each consumer project (mermaid `.mmd` sources with a
required five-field header, a generated `README.md`, a `workflows.json` index, optional light/dark
PNG rasters, and optional `<id>.layout.json` sidecars drawn as house-style SVGs). The documents live
in the consumer; this project owns templates, checks and generators. Authoritative shapes:
[`docs/plans/master_plan.md`](docs/plans/master_plan.md) §3, §4, §8.

## Stack

| Layer | Tool |
|---|---|
| Runtime | Python >= 3.12, uv; stdlib-only runtime for `init` / `check` / `render` |
| Packaging | hatchling, `src/` layout, console script `show-your-work` |
| CLI | argparse |
| Optional renderer | `mermaidx==0.9.5` and `resvg-py==0.5.0` (house PNGs) in extra `render` (for `check --render`, `render --png`) |
| Quality | pytest, ruff, mypy `--strict` in extra `dev` |

## Commands

Shell: PowerShell (primary) on Windows; commands are shell-agnostic via uv.

- `uv sync --extra dev` — create env + install dev tools
- `uv sync --extra dev --extra render` — also install the pinned renderer
- `uv run pytest -q` — tests
- `uv run ruff check .` — lint
- `uv run ruff format --check .` — format check (never run write-mode across the repo)
- `uv run mypy --strict src tests` — types
- `uv run show-your-work init [TARGET] [--force]` — scaffold a `docs/workflows/` folder
- `uv run show-your-work check [TARGET] [--render] [--json]` — validate headers, budget, structure
- `uv run show-your-work render [TARGET] [--check] [--png] [--json]` — regenerate README + index (+ rasters)
- `uv run --project ../utility-project-standard utility-standard check .` — standard conformance (manifest `utility-project.toml`)
- `uv run --project ../utility-project-standard utility-standard audit-publication .` — public-repo leak audit (tracked files + history)

## Layout

- `src/show_your_work/constants.py` — the ONE home for budget numbers, header bounds, id regex, schema strings, raster constants, garbage fixture
- `src/show_your_work/header.py` — the single header parser (title, Summary, Sources, Not shown, Alt)
- `src/show_your_work/checks.py` — `check` logic and findings
- `src/show_your_work/layout.py` — layout sidecar (`<id>.layout.json`) parse and validation, and the house SVG engine `draw_svg`
- `src/show_your_work/renderer.py` — optional mermaidx wrapper, garbage anchor, SVG counts, PNG; house PNGs via `resvg_py` (`house_png`, house anchors)
- `src/show_your_work/fonts/` — bundled DejaVu Sans Mono + Bold and `LICENSE-DejaVu.txt` (house PNGs; pinned SHA-256s, shipped in the wheel)
- `src/show_your_work/render.py` — README + `workflows.json` builders, `--check`, house SVGs, rasters
- `src/show_your_work/scaffold.py` — `init`
- `src/show_your_work/cli.py` — argparse, exit codes, `--json`
- `templates/workflows/` — `intro.md`, `example-workflow.mmd` copied by `init`
- `tests/` — `test_header`, `test_checks`, `test_render`, `test_scaffold`, `test_cli`, `test_png` (renderer; skips without the `render` extra), `test_realistic_fixture`, `test_live_coding_root` (read-only tmp copy; skips when absent), `test_regex_linear`, `test_layout` (incl. the README "Layout sidecar" example), `test_layout_svg`; `fakes.py` test doubles
- `tests/fixtures/` — `garbage/`, `bad-headers/`, `over-budget/`, `good/`, `realistic/`, `layout/` (sidecar cases, `phase1/` goldens); all synthetic (the real coding-root diagrams are private and never committed)
- `docs/workflows/` — this repo's own dogfooded folder; `example-workflow` has a sidecar and committed house SVGs in `rasters/` (PNGs are never committed)
- `docs/migration-coding-root.md` — M1 procedure; `docs/observatory-amendment.md` — M2 issue body
- `README.md` + `utility-project.toml` — utility-project-standard conformance (`standalone-cli` / `public` / `experimental`)

## Architecture

Flat files only. A consumer's `docs/workflows/` holds hand-authored `intro.md` and `.mmd` files
(plus optional `.layout.json` sidecars) and generated `README.md` / `workflows.json` / `rasters/`
(house SVGs from every `render`, PNGs from `render --png`). `header.py` is the one parser;
`checks.py` produces findings with stable codes; `render.py` refuses to generate from a failing set.
Every render path renders a known-garbage fixture first and aborts (exit 2) if it does not raise.
Exit codes: 0 pass, 1 findings or stale, 2 gate could not run, 64 usage.

## Current state

Phase 1 complete — automated Steps 1-7 built (2026-10-24; issues #2-#8 closed): `init` / `check` / `render` (+ `--render`, `--png`), templates, dogfooded `docs/workflows/`, README and standard conformance. Operator handoff in plan order: M1 "Migrate `dev/docs/workflows/` to the standard" (`docs/migration-coding-root.md`), then M2 "File the dev-observatory plan amendment" (`docs/observatory-amendment.md`).

Phase 2 (house-style layout output, plan "Phase 2") — Steps 8-10 DONE (2026-10-29; issues #13-#15): the layout sidecar contract in `check`, the house SVG engine written by every `render` and embedded in README via `<picture>`, and house PNGs through `resvg_py` with the bundled fonts. Step 11 (#16) amends the contract text, documents the sidecar in README and dogfoods it (461 tests). Next is Manual M3 (#17): consumer acceptance in the project that reported the defect.

## Environment

Windows 11 + PowerShell; Python 3.12+ via uv. No network, secrets or services. `mermaidx` only when
the `render` extra is installed. Raster hashes are not guaranteed across machines or renderer
versions (plan risk: host fonts); mermaidx 0.9.5 was observed to skip system fonts and use its
bundled font, but cross-machine stability is unverified. House PNGs draw only with the bundled
DejaVu fonts. `render --check` ignores raster hashes unless `--png`.

## Gotchas

- `init` never overwrites user files; `--force` touches template-origin files only.
- `workflows.json` is this tool's index (`show-your-work.workflows.v1`), not the Observatory
  `devobs.workflows.v1` envelope; dev-observatory owns that mapping.
- The coding root `dev/docs/workflows/` is a different git repo; its migration is manual step M1.
