# Magpie v0.1.0 -- Pre-Tag Release Checklist

Every item below must be GREEN before tagging v0.1.0. This is the required gate
from the Phase 11 design (section 5.3). The heavy paths are gated at the release
cut here -- not "validated sometime during the phase."

Run all Python via the venv interpreter on Windows PowerShell:
`& .venv\Scripts\python.exe ...` (never bare `python`).

## Required-green gate

- [ ] **Offline CI green.** The offline subset passes on push/PR:
      `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
      (the GitHub Actions `offline` job is the source of truth).

- [ ] **Heavy suite green.** The `workflow_dispatch` heavy job is green, OR the
      equivalent local heavy run is green:
      `& .venv\Scripts\python.exe -m pytest -m "docling or spacy or xray" -q`.

- [ ] **TSA live test green locally.** The freeTSA round-trip passes locally
      (it is network-flaky, so it stays a local pre-tag step, not CI):
      `& .venv\Scripts\python.exe -m pytest -m tsa -q`.

- [ ] **Bundled public artifact sha256s match.** Every bundled artifact in
      `corpus/public/` has a sha256 that matches the value recorded in
      `corpus/public/DATASHEET.md`.

- [ ] **MANIFEST.md regenerated.** The repo-root `MANIFEST.md` reflects the final
      Phase 11 structure (regenerated in the main thread; it carries non-ASCII).

- [ ] **Librarian auto-pull acceptance test still green.** The acceptance test
      lives in the librarian repo; confirm Phase 11 did not break the dependency
      contract. This is a check, not a re-run here.

- [ ] **plugin.json version is 0.1.0.** `.claude-plugin/plugin.json` declares
      `"version": "0.1.0"`.

## Tag

- [ ] After the Phase 11 PR merges to `main`, tag the release:
      `git tag v0.1.0`.
