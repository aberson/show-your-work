# show-your-work — project instructions

Workflow transparency utility: one centralized standard and CLI that scaffolds, validates and
renders a `docs/workflows/` folder inside each consumer project (mermaid `.mmd` sources with a
required five-field header, a generated `README.md`, a `workflows.json` index, optional light/dark
PNG rasters). The documents live in the consumer; this project owns templates, checks and
generators. Authoritative shapes: [`docs/plans/master_plan.md`](docs/plans/master_plan.md) §3, §4, §8.

## Stack

| Layer | Tool |
|---|---|
| Runtime | Python >= 3.12, uv; stdlib-only runtime for `init` / `check` / `render` |
| Packaging | hatchling, `src/` layout, console script `show-your-work` |
| CLI | argparse |
| Optional renderer | `mermaidx==0.9.5` in extra `render` (for `check --render`, `render --png`) |
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
- `uv run --project ../utility-project-standard utility-standard check .` — standard conformance

## Layout

- `src/show_your_work/constants.py` — the ONE home for budget numbers, header bounds, id regex, schema strings, raster constants, garbage fixture
- `src/show_your_work/header.py` — the single header parser (title, Summary, Sources, Not shown, Alt)
- `src/show_your_work/checks.py` — `check` logic and findings
- `src/show_your_work/renderer.py` — optional mermaidx wrapper, garbage anchor, SVG counts, PNG
- `src/show_your_work/render.py` — README + `workflows.json` builders, `--check`, rasters
- `src/show_your_work/scaffold.py` — `init`
- `src/show_your_work/cli.py` — argparse, exit codes, `--json`
- `templates/workflows/` — `intro.md`, `example-workflow.mmd` copied by `init`
- `tests/fixtures/` — `garbage/`, `bad-headers/`, `over-budget/`, `good/`, `realistic/` (synthetic; the real coding-root diagrams are private and never committed)
- `docs/workflows/` — this repo's own dogfooded folder

## Architecture

Flat files only. A consumer's `docs/workflows/` holds hand-authored `intro.md` and `.mmd` files
and generated `README.md` / `workflows.json` / `rasters/`. `header.py` is the one parser; `checks.py`
produces findings with stable codes; `render.py` refuses to generate from a failing set. Every
render path renders a known-garbage fixture first and aborts (exit 2) if it does not raise. Exit
codes: 0 pass, 1 findings or stale, 2 gate could not run, 64 usage.

## Current state

Plan written 2026-10-24 (`docs/plans/master_plan.md`), no code yet. Next: `/plan-expedite --plan docs/plans/master_plan.md`, then `/build-phase`.

## Environment

Windows 11 + PowerShell; Python 3.12+ via uv. No network, secrets or services. `mermaidx` only
when the `render` extra is installed. Rasters are host-font dependent (resvg): hashes differ across
machines.

## Gotchas

- `init` never overwrites user files; `--force` touches template-origin files only.
- `workflows.json` is this tool's index (`show-your-work.workflows.v1`), not the Observatory
  `devobs.workflows.v1` envelope; dev-observatory owns that mapping.
- The coding root `dev/docs/workflows/` is a different git repo; its migration is manual step M1.
