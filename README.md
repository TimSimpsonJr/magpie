# Magpie

**Fieldwork: Magpie** — a FOSS-first investigative analysis toolkit for Claude Code.

Magpie helps an investigator analyze material obtained through FOIA / public-records
requests and structured data releases. Like the bird, it gathers scattered shiny
things — documents, structured data, entities — into one analyzable, citable nest,
and routes every headline claim through a verification gate before it is treated as
publishable.

> **Status:** Layer 0–1 (laptop-local flagship) under active development.
> Full design: [`docs/plans/2026-06-03-magpie-design.md`](docs/plans/2026-06-03-magpie-design.md).

## Two tracks

- **Track A — Dataset analysis (flagship, Layer 1).** Quantitative analysis of large
  structured FOIA releases (CSV/XLSX): concentration statistics, automation
  signatures, repeatable per-source recipes that roll up across sources, and PII
  sweeps. Pure local Python — no heavy Docker.
- **Track B — Entity-network analysis (Layer 2).** Documents → entity + relation
  extraction → resolution → graph → cross-reference against watchlists. Not part of
  Layer 0–1.

## Getting started

Magpie has two onramps (authored during the setup phase):

- **Operators** — one-time technical setup (venv, model downloads, MCP wiring):
  `docs/OPERATOR_GUIDE.md`.
- **Investigators** — daily conversational use, no infrastructure to manage:
  `docs/JOURNALIST_START.md`.

Layer 0–1 runs on a laptop with no heavy infrastructure: pandas + DuckDB, spaCy,
Docling, and Free Law `x-ray`, queried through a pinned read-only `mcp-sqlite`.

## Development

The dev environment is managed by [mise](https://mise.jdx.dev) (`mise.toml`),
which pins the Python toolchain (3.12.10) and binds the project virtualenv.

- **Run the tests:** `mise run test` (the full suite).
- **Rebuild the venv** from `requirements-dev.txt`: delete `.venv`, then `mise run bootstrap`.
- **Ad-hoc Python in the venv:** `mise exec -- python ...`.

> **Windows / PowerShell note.** mise's shell *activation* (bare `python` /
> `pytest`) relies on a prompt hook that only fires in an interactive shell.
> Non-interactive one-shot shells — including the Claude Code PowerShell tool,
> which runs `-NoProfile` — won't auto-activate, so use `mise run` / `mise exec --`
> there. **Do not call bare `python`** in such a shell: it resolves to the global
> interpreter, whose dependency versions may differ from the pinned venv. The
> always-works fallback is the explicit venv path: `& .venv\Scripts\python.exe -m pytest`.

`tools/codex-review.ps1` runs a Codex cross-model review with UTF-8 pinned
end-to-end (a workaround for PowerShell 5.1's cp1252 pipe encoding); used at
phase boundaries while the build is on `main`.

## License

MIT — see [LICENSE](LICENSE). The default tool stack is permissively licensed;
copyleft / non-free tools are clearly-labeled opt-in profiles only (design doc §8).

Magpie hard-depends on **Librarian** (structured findings notes) and softly couples
to **Research** (web corroboration) and **Prose Craft** (outward-facing write-ups).
