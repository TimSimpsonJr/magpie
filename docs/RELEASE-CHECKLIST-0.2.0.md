# Magpie v0.2.0 -- Pre-Tag Release Checklist

v0.2.0 adds Track B (Layer 2, Docker-gated). The gate below adds the Track-B CI
jobs to the v0.1.0 gate; the heavy/TSA/public-artifact items from v0.1.0 are
unchanged (Track B does not touch them). Run Python via the venv interpreter on
Windows PowerShell: `& .venv\Scripts\python.exe ...` (never bare `python`).

## Required-green gate

- [ ] **Offline CI green.** The offline subset passes on push/PR (the GitHub
      Actions `offline` job is the source of truth):
      `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa and not gliner and not ftm and not neo4j and not compose and not yente" -q`

- [ ] **Track-B CI jobs green (the only real surface for the Linux/Docker edges).**
      Gate the merge on ALL of: `ftm` (followthemoney/nomenklatura contract),
      `graph` (live Neo4j service container), `compose` (graph-profile bring-up),
      `crossref` (live OpenSearch 2.19.5 + yente 5.4.0: real `/match` + yente-mcp
      smoke). Never tag on Windows-green alone.

- [ ] **MANIFEST.md regenerated** to the budget (guarded by
      `tests/test_manifest_budget.py`).

- [ ] **Librarian dependency contract intact** (`dependencies: ["librarian"]`;
      librarian unchanged at 0.1.0). A check, not a re-run.

- [ ] **Version bumped.** `.claude-plugin/plugin.json` declares `"version":
      "0.2.0"`; `scripts/evidence.py` `tool_version` default is `"0.2.0"`.

## Tag

- [ ] After the release PR merges to `main`, tag the release on the merge commit:
      `git tag v0.2.0 && git push origin v0.2.0`.
