# Migrating the coding root's `docs/workflows/` (manual step M1)

The coding root (the directory that holds this project as `show-your-work/`) keeps its own
workflow diagrams in `docs/workflows/`. That folder lives in a **different, private git
repository**: its diagrams, Alt text and intro prose never enter this public repo (plan P6). This
document describes the migration procedure only; it copies none of that content.

Every command below runs from the coding root unless stated otherwise. Paths are relative.

## Before you start

- This project is built and its full gate is green (`uv run pytest -q` in `show-your-work/`).
- `git -C . status --porcelain docs/workflows` is empty, so the migration diff is reviewable on
  its own.
- Read the header contract in `show-your-work/docs/plans/master_plan.md` section 3.3: every
  diagram needs a title line plus `Summary:`, `Sources:`, `Not shown:` and `Alt:`.

## 1. Preview: the set still passes the old gate

```powershell
python docs/workflows/check_diagrams.py
```

Expected: every diagram passes. Do not continue on a failure; fix it under the old gate first.

Optional: confirm what the new tool reports before any edit. The live test copies the set to a
temporary directory and runs `check` and `render` on the copy, so it never writes in place:

```powershell
uv run --project show-your-work pytest show-your-work/tests/test_live_coding_root.py -rs -v
```

Before migration it prints `state=pre-M1` and passes only if `header-missing-alt` is the one
finding code across the set and `render` refused to write anything.

## 2. Author the private content (coding-root repo only)

In a fresh session at the coding root:

1. Add one `%% Alt: ...` line directly after the `%% Not shown:` line of each
   `docs/workflows/*.mmd`. Describe the picture (shapes, direction, branches); never repeat the
   title; at most 240 printable ASCII characters; change no other bytes. A file with CRLF line
   endings keeps CRLF.
2. Write `docs/workflows/intro.md` carrying the prose and the document table that the prototype
   `docs/workflows/render_readme.py` embeds in its header, with links relative to
   `docs/workflows/`. The table keeps its rows for the non-diagram documents that stay in the
   folder; `check` ignores non-`.mmd` files.

## 3. Retire the prototype scripts and regenerate with the tool

```powershell
Remove-Item docs/workflows/check_diagrams.py, docs/workflows/render_readme.py
Remove-Item -Recurse -Force docs/workflows/__pycache__ -ErrorAction SilentlyContinue
uv run --project show-your-work show-your-work check docs/workflows
```

Expected: `N/N pass`, exit 0, and no `header-missing-alt`. Then:

```powershell
uv run --project show-your-work show-your-work render docs/workflows
git -C . status --porcelain docs/workflows
```

Re-run the live test; it now prints `state=post-M1` and expects `check` and `render` to exit 0:

```powershell
uv run --project show-your-work pytest show-your-work/tests/test_live_coding_root.py -rs -v
```

## 4. Commit at the coding-root layer only

Path-scoped: never `git add -A` at the coding root, which would sweep nested project repos.

```powershell
git add docs/workflows
git commit -m "docs/workflows: migrate to show-your-work standard (Alt headers, generated README + index)"
```

## What to look for

| Check | Expected outcome |
|---|---|
| Step 1 old gate | every diagram passes |
| `show-your-work check` | `N/N pass`, exit 0, no `header-missing-alt` |
| `README.md` diff | the same per-diagram sections, now with `**Alt:**` lines; the document table survives inside the intro; the generated marker names `show-your-work` |
| `workflows.json` | one item per diagram, ids sorted, `engine: "mermaid"` everywhere, `rasters: null` |
| `git status` | only `docs/workflows/` changed; the two prototype `.py` files deleted; nested project repos untouched |
| Live test | `state=post-M1`, 2 passed, 0 skipped |

## Rollback

Everything changed lives in the coding-root repo's `docs/workflows/`. Before the commit,
`git -C . restore --source=HEAD --staged --worktree docs/workflows` returns the folder to its
pre-migration state (and `git -C . clean -n docs/workflows` previews the generated files to
remove). After the commit, revert that one commit.
