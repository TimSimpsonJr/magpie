---
name: doctor
description: Use when a journalist or investigator wants to check whether Magpie is healthy on this machine -- which analysis and document workflows are ready, what is missing, and what to ask their operator to fix. This is a strictly read-only health check; it installs nothing. Invoke it whenever a user wants to run a health check, diagnose why a Magpie skill is unavailable, or see what Magpie can currently do.
version: 0.1.0
---

The doctor skill is the everyday, read-only health check for Magpie: it tells a
journalist which analysis and document workflows are ready right now, what is missing,
and what to ask their operator to fix. It is driven by the shared capability engine
`scripts/detect_tier.py`, which it runs read-only, probing the toolchain without
importing heavy stacks, without changing the machine, and without starting any
service. The operator can run the same probe outside Claude Code with
`& .venv\Scripts\python.exe scripts\detect_tier.py`.

## 1. What doctor does

Doctor runs `scripts/detect_tier.py` and presents the result. It is safe to run
anytime, by anyone, as often as you like: it only reads the state of the machine.

## 2. What doctor reports

Doctor renders the capability map in user verbs (analyze datasets, ingest native
PDFs, OCR preprocessing for scans, PII scan, redaction QA, citation verify, evidence
timestamp, and extract entities (Track B)), plus the subordinate two-line headline
(core structured-data analysis, and the document-workflows rollup; the Track-B
entity-extract capability is independent of both). Doctor also reports the Layer-2
"build an entity graph" capability via a READ-ONLY Docker probe -- it runs
`shutil.which("docker")` plus `docker version` / `docker compose version` for their
return codes only, and NEVER pulls an image or starts a container. The Layer-2
"cross-reference entities" capability (Phase 13b, yente + OpenSearch) is the second
Docker-gated capability and is reported off the SAME read-only Docker probe; doctor
never probes a live yente. (Installing Docker
for that capability is setup's job, not doctor's; doctor only reports it.) For each
gap it shows what that gap blocks and the single next instruction: ask your operator
to run setup, or, for a missing system binary, the one-line hint naming the binary to
install. Doctor reports a capability map, never a single linear tier score.

## 3. The read-only contract

Doctor is strictly read-only. Doctor NEVER installs anything, NEVER runs
`mise run bootstrap`, NEVER invokes setup, and NEVER starts the mcp-sqlite server. For
the conversational query surface it only checks that uvx exists on PATH and that the
project .mcp.json declares the mcp-sqlite server; it does not execute uvx and does not
launch the server. For the Layer-2 entity-graph capability it only probes Docker
read-only -- which plus the `docker version` / `docker compose version` return codes;
it NEVER runs `docker run`/`pull`/`up`/`start`, pulls an image, or starts a container.
Anything that changes the machine is the job of the setup skill and a present operator,
not doctor. If doctor reports something missing, the fix is to ask whoever set this up
to run setup.

## 4. Honest limit

`scripts/detect_tier.py` reports presence and version, not correctness. A capability
shown as ready means its dependencies are installed and resolvable by name, not that
every downstream tool will succeed on a given document. Read the report as a current
inventory of what the machine can do, not as a promise that any single run will work.
