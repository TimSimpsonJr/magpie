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

## License

MIT — see [LICENSE](LICENSE). The default tool stack is permissively licensed;
copyleft / non-free tools are clearly-labeled opt-in profiles only (design doc §8).

Magpie hard-depends on **Librarian** (structured findings notes) and softly couples
to **Research** (web corroboration) and **Prose Craft** (outward-facing write-ups).
