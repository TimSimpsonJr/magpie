# Magpie Phase 11 -- Validation & Release (Design)

Date: 2026-06-05
Status: Design -- brainstorm-converged with the Codex partner on threaded session
019e95ea-d5d9-72f0-87db-e1bbd50a4c42. This doc is the source of truth for the
Phase 11 implementation plan.
Phase: 11 of the Layer 0-1 build -- the FINAL phase. After this merges, Layer 0-1
is COMPLETE (Track B / entity-network + any Docker infra is Layer 2, out of scope).
Plan: docs/plans/2026-06-05-magpie-phase11-validation-release.md (written next by
writing-plans).

ASCII-only by contract (SDD subagents read this file).

--------------------------------------------------------------------------------
## 1. Why
--------------------------------------------------------------------------------

Phases 0-10 built the entire Layer 0-1 engine surface: the deterministic stats
flagship, the load -> quality-gate -> derive -> served-DB pipeline, the 13-point
analysis recipe + cross-source rollup, the spaCy PII sweep, the Docling/RapidOCR
ingest path, the redaction-check / redact-output pair, the investigate citation
anchor, the archive-evidence provenance/custody engine, and the setup/doctor
onboarding probe. 494 offline tests are green (1 skipped).

Phase 11 does NOT add engine features. It VALIDATES that engine against reality
and CUTS the v0.1.0 release. Three deliverables:

- 11.1 A clean, redistributable, PII-FREE PUBLIC sample corpus so a new user can
  "try it now" and so CI has a deterministic golden source.
- 11.2 GOLDEN TESTS that pin the engine to the documented Simpsonville pilot
  reality (the real, private corpus -- NEVER committed).
- 11.3 STRUCTURAL SMOKE + CI + the v0.1.0 tag.

The only new runtime code is small (test adapters, a slice-builder script, CI
config, smoke tests). The rigor bar for this phase is HONESTY: be exact about
what is validated, and be exact about what actually ships in v0.1.0.

--------------------------------------------------------------------------------
## 2. Goals / Non-goals
--------------------------------------------------------------------------------

Goals:
- Bundle an authentic, PII-scrubbed, frozen, characterized public fixture (a CSV
  slice + a scanned PDF) with a provenance datasheet and expected-output goldens.
- Pin the analysis engine to the documented pilot values via env-gated golden
  tests over the private corpus; also close issue #6 (citation-anchor revalidation).
- Stand up CI (offline default + a manual heavy job), structural + install smoke,
  release notes, a pre-tag checklist, and tag v0.1.0.

Non-goals:
- No new analysis-engine features. No Track B / Layer 2. No Docker. No new deps.
- The public corpus is NOT claimed representative of the full RANGE release. It is
  an authentic, PII-scrubbed, frozen, CHARACTERIZED fixture. Full stop.
- v0.1.0 does NOT block on RANGE Media's re-host permission (see 3.7 / section 6).

--------------------------------------------------------------------------------
## 3. Task 11.1 -- Public sample corpus
--------------------------------------------------------------------------------

### 3.1 Artifacts
- CSV: a frozen, PII-scrubbed SLICE of RANGE Media's already-published "Spokane
  County Flock network audit" (authentic Flock schema: searching agency, free-text
  reason, case number, network/camera counts, timestamps; license plates already
  removed by RANGE). Exercises load -> data_quality -> derive -> recipe -> stats.
- PDF: the Skokie PD Flock FOIA cover-letter (image-heavy -> exercises OCR + the
  Phase-8 citation anchor), published via MuckRock.

### 3.2 Asset distribution -- a deterministic NEUTRAL slice (not the full file)
The full RANGE CSV is ~2M rows (hundreds of MB) -- too big for git (clone time +
GitHub file-size limits; a plugin repo must stay lean). We bundle a small frozen
SLICE produced by a COMMITTED, reproducible script (tools/build_public_slice.py)
using a NEUTRAL, deterministic rule. A characterization fixture must NOT cherry-pick
("preserves enough distribution to look meaningful" is editorial and forbidden):

1. Load the full RANGE CSV (local working copy, gitignored, NEVER committed).
2. At most ONE documented STRUCTURAL validity filter (e.g. drop fully-empty rows).
   Never an outcome-based filter.
3. Stable TOTAL-ORDER sort by (Org Name, Search Time, then the remaining columns as
   a deterministic tiebreaker) ascending.
4. Take the FIRST N rows. N is fixed PURELY for file size (committed slice target
   well under a few MB), pinned in the script -- NOT tuned to make outputs look good.
5. PII-scrub the slice (3.3).
6. Freeze as the committed fixture; record the final sha256 + the exact rule + N +
   the scrub method in the datasheet.

Anyone can re-run the script against the RANGE source to reproduce the slice
byte-for-byte. Describe the fixture EXACTLY as: "an authentic public fixture from
the real source, PII-scrubbed, frozen, characterized." No representativeness claim.
Rejected alternative: a fixed-seed stratified sample -- more machinery, and
"stratify to preserve distribution" slides back toward an editorial goal; sort +
head-N is the more obviously-neutral rule.

### 3.3 PII-scrub gate (dogfooding our own tools)
Before bundling EITHER artifact, run Magpie's OWN tools on it:
- CSV reason column: pii_sweep over the slice's free-text reason. If ANY PII
  (person names or structured PII), either redact via redact_output (typed
  placeholders; uninvolved names -> initials) OR DROP the reason column entirely;
  then re-scan to confirm clean. The bundled FILE itself must be PII-clean -- the
  build_dataset_db include_columns allowlist only cleans the SERVED db, never the
  raw bundled CSV that we re-host.
- Skokie PDF: ingest -> redaction_check (received mode) + pii_sweep over the
  extracted text; human eyeball for stray signatures. Bundle only if clean.

### 3.4 Provenance datasheet (release-critical)
corpus/public/DATASHEET.md (ASCII). Per bundled artifact: source URL, attribution
(RANGE Media; Skokie PD via MuckRock), license / permission posture, vetting date,
the PII-scrub method, the slice rule + N (CSV), and the final sha256. A FOIA / PII
tool MUST carry a provenance record for its own bundled data -- this dogfoods the
ethic the tool enforces on its users.

### 3.5 Permission record
RANGE re-host permission is obtained out-of-band: a drafted email Tim sends (3.7).
The APPROVAL (date + grant) is referenced in the datasheet even though the raw
email is never committed. The RANGE slice is NOT committed until permission is
recorded AND the slice is PII-clean.

### 3.6 Expected-output fixtures (the golden + the "try it now")
Run the magpie pipeline on the bundled artifacts; freeze the deterministic outputs
as golden fixtures (tests/golden/public/). The CSV path (load -> data_quality ->
derive -> recipe -> stats) is OFFLINE (pure pandas/numpy) so it runs in default CI.
The PDF full-OCR golden is docling-marked (heavy / local). These public goldens are
a CHARACTERIZATION of magpie's output ON THESE FIXTURES -- they are NOT the pilot
values (those are validated separately in 11.2 against the private corpus).

### 3.7 Sequencing / decoupling (v0.1.0 is not hostage to RANGE's inbox)
Build ALL 11.1 MACHINERY now: the slice script, the PII-scrub harness, the
datasheet, the fixture generator, and CI public-sample goldens that SKIP-IF-ABSENT
(like an env-gated test) so the suite is green whether or not the corpus file is
present yet. The Skokie PDF (a published FOIA doc on MuckRock) can bundle as soon
as it passes vetting. The RANGE slice bundles when permission + a clean pii-sweep
clear; if the email is slow, it is a fast-follow commit (or v0.1.1). v0.1.0 release
notes state the corpus status HONESTLY: what shipped, what is pending. v0.1.0 must
not CLAIM the corpus is shipped if the RANGE asset is not in hand, permission is
not recorded, and the file is not PII-clean.

--------------------------------------------------------------------------------
## 4. Task 11.2 -- Golden tests vs the private Simpsonville corpus
--------------------------------------------------------------------------------

### 4.1 Gating
tests/golden/test_simpsonville.py, gated on an env var (MAGPIE_SIMPSONVILLE_CORPUS)
pointing at the private corpus folder (the ~1M-row Network-Audit.csv + the 492-row
Audit.csv). The corpus is NEVER committed (.gitignore already blocks it). A pytest
skipif on the unset env var makes the offline suite skip it cleanly. The
spaCy-dependent PII assertion is ALSO spacy-marked; the citation-anchor
re-validation is docling-marked and reuses the existing MAGPIE_PHASE8_REAL_PDF
env-var pattern (Phase-8 Tier-2b) so issue #6 is closed here.

### 4.2 The Simpsonville pipeline-configuration adapter (documented, not test magic)
A small DOCUMENTED helper tests/golden/_adapters.py carries the Flock-format
adapters that map the raw Flock CSV columns onto magpie's generic derive config --
the same glue the pilot's own clean() did. It is explicitly "how a real Simpsonville
run configures the generic engine," NOT hidden test magic:
- extract_state(org) -> the last US-state token in a free-form Org Name (with a
  "South Carolina" spelled-out special case) -> fed to derive_geo(source_col=
  'state', home_value='SC'). This keeps derive_geo GENERIC (no US-state extractor
  baked into a primitive -- Decision 2).
- reason_cat = Reason.split(' - ')[0] (the Flock standardized category) -> fed to
  the blast-radius-by-category check and to recipe.check_pretext (cat_col path).
The remaining derivations map through magpie's derive DIRECTLY: immigration via
derive_immigration keyword "immigration" (or the exact category match); nets;
has_case with the "***" redaction sentinel; temporal UTC -> America/New_York.

### 4.3 Assertions -- CHARACTERIZATION over a FIXED corpus
Because the corpus is fixed, every engine output is deterministic -> assert EXACT
values, with explicit tolerance ONLY where genuinely nondeterministic. No "~" in
assertions. Each exact value is computed once on the real corpus at implementation
time and pinned with a comment tying it to the documented pilot figure.

- truncation: loaded row count == 1048575 AND data_quality.check_truncation flags
  it at the 2^20-1 ceiling. EXACT.
- out_of_state: pin the exact OOS row count (which rounds to the documented 89.7%),
  via stats.category_pct(df, df["geo"] == "OOS") -- a single FRACTION (mask.mean()),
  NOT a label map. EXACT.
- immigration: pin the exact de-duplicated criminal-OR-civil count (documented
  ~770), via derive_immigration / the Flock category match. EXACT.
- pretext: pin the exact recipe.check_pretext count (cat_col=reason_cat,
  pretext_cats=<pinned Flock category set>); it must satisfy the documented >=175.
  EXACT count, annotated.
- Gini: round(stats.gini(per-Org row counts), 3) == 0.805. EXACT to 3 dp.
- blast_radius: stats.median_by_category(nets, reason_cat). Assert Traffic median >
  Homicide median (strict) AND pin both exact medians (documented ~2943 and ~1792).
- PII: pii_sweep over the reason text. Distinct-agency count == 747 (EXACT).
  Exposure tally within an EXPLICIT numeric band around 11,900 (TOLERANCE -- spaCy
  model-version sensitivity is the one genuine source of nondeterminism).
- citation #6: reuse MAGPIE_PHASE8_REAL_PDF; assert the anchor resolves clean
  (exact/relocated, no false exact) on the real Greenville RFP (Tier-2b pattern).

### 4.4 Performance
The PII assertion over ~1M rows relies on pii_sweep's distinct-then-weight (NER over
DISTINCT reason texts, not 1M rows). It is still the heaviest test; env-gated +
spacy-marked so it runs only when explicitly requested.

--------------------------------------------------------------------------------
## 5. Task 11.3 -- Structural smoke + CI + release
--------------------------------------------------------------------------------

### 5.1 Two-tier CI (GitHub Actions; greenfield -- no .github yet)
- DEFAULT job (on push / PR; ubuntu-latest, Python 3.12): install the offline deps;
  run a detect_tier --json pre-flight; run the offline subset
  -m "not docling and not spacy and not xray and not tsa" (~494 tests) + the
  public-sample CSV goldens (offline, skip-if-absent) + structural smoke. Fast
  (minutes), no 2 GB models. ubuntu-latest so the 1 Windows-only-skipped dir-symlink
  test actually RUNS.
- HEAVY job (workflow_dispatch, manual): install the ~2 GB docling/spacy/xray stack;
  run -m "docling or spacy or xray" INCLUDING the full Skokie OCR + citation-anchor
  golden. The tsa live freeTSA round-trip stays a LOCAL pre-tag step (network-flaky).

This is Decisions 3 + 4: offline-only default CI (do not pay 2 GB per push), but a
REAL heavy gate exists and is REQUIRED at release cut (5.3) -- not "validated
sometime during the phase."

### 5.2 Structural + install smoke (in the default job)
- Plugin-load / install smoke: plugin.json parses + required keys + the librarian
  dependency; every SKILL.md frontmatter parses (name/description); .mcp.json parses
  with valid ${CLAUDE_*} interpolation. A consolidated "the plugin loads" assertion
  (extends the existing manifest + skill smoke tests).
- recipe end-to-end on the public CSV slice (offline) -- skip-if-absent.
- mcp-sqlite start smoke: build a tiny dataset.db, launch
  uvx mcp-sqlite==0.3.2 ..., confirm it serves a canned query, shut it down
  (requires uv in CI). This is the one piece that proves the served-DB path
  end-to-end. Fallback if flaky in CI: detect_tier.check_mcp_wiring (read-only) +
  assert uvx is on PATH. The plan picks one; default to the real start smoke with
  the wiring check as the documented fallback.
- detect_tier --json pre-flight (the natural CI capability check; fail-fast if a
  core dep is missing).

### 5.3 Release
- Bump .claude-plugin/plugin.json version 0.0.1 -> 0.1.0.
- Regenerate MANIFEST.md (MAIN thread -- it carries non-ASCII).
- Release notes (docs/RELEASE-NOTES-0.1.0.md, ASCII): what ships, what is deferred
  (Track B / Layer 2 / Docker), and the public-corpus status (Skokie shipped if
  vetted; RANGE slice pending / fast-follow).
- Pre-tag RELEASE CHECKLIST (committed -- in the release notes or a
  docs/RELEASE-CHECKLIST.md): all REQUIRED green before tagging:
  offline CI green; the heavy job (or the equivalent local heavy run) green; the
  tsa live test green locally; the bundled public artifacts' sha256s match the
  datasheet; MANIFEST regenerated; the Librarian acceptance test still green.
- Tag magpie v0.1.0 (after the PR merges to main).
- Keep the Librarian auto-pull acceptance test green: it lives in the librarian
  repo; Phase 11 must not break the dependency contract. A check, not a re-run here.

--------------------------------------------------------------------------------
## 6. Sequencing & dependency management
--------------------------------------------------------------------------------

The Phase 11 PR contains: 11.2 (private goldens), 11.3 (CI + smoke + version bump +
release notes + checklist), and ALL 11.1 machinery (slice script, scrub harness,
datasheet, fixture generator, skip-if-absent public goldens), plus the Skokie PDF +
its goldens IF vetting passes in time. The PR does NOT block on RANGE's permission.

v0.1.0 tags when the PR is merged and the pre-tag checklist is green. The authentic
RANGE slice + its frozen goldens land via a fast-follow commit (or v0.1.1) once
permission is recorded and the slice passes pii-sweep. If RANGE declines or goes
silent indefinitely, the documented fallbacks from the corpus research remain (a
PII-safe EFF aggregate CSV under CC BY 4.0, or a Chicago open-data slice) -- a later
decision, not a v0.1.0 blocker.

--------------------------------------------------------------------------------
## 7. Testing strategy
--------------------------------------------------------------------------------

- The offline subset stays green throughout (-m "not docling and not spacy and not
  xray and not tsa"; currently 494 passed / 1 skipped).
- New tests: public-slice goldens (offline, skip-if-absent), the 11.2 private
  goldens (env + spacy + docling gated), structural + install smoke, the mcp-sqlite
  start smoke. SDD + TDD: write the failing assertion as the spec, then implement.
- Markers: reuse spacy / docling / xray / tsa; the env-gated private goldens use a
  skipif, not a new marker (so the offline -m exclusion already skips them, and an
  unset env var is a clean skip rather than a failure).

--------------------------------------------------------------------------------
## 8. Risks / open
--------------------------------------------------------------------------------

- RANGE permission slow / denied -> the slice is a fast-follow / v0.1.1; the
  decoupling protects the tag; documented fallbacks exist.
- Skokie PII vetting fails -> drop the PDF (or use a federal public-domain fallback
  PDF, accepting weaker OCR exercise); then there is no Skokie OCR golden.
- mcp-sqlite CI start flaky -> fall back to the read-only wiring check + uvx-present.
- spaCy PII tally cross-platform variance -> the explicit band absorbs it.
- CI dep install: torch/docling CPU wheels are large even when we only RUN the
  offline subset; the plan decides whether the default job installs the full
  requirements-dev.txt or a trimmed offline subset (cache wheels via actions/cache).

--------------------------------------------------------------------------------
## 9. Decisions log (brainstorm-converged with the Codex partner)
--------------------------------------------------------------------------------

- D1 Sequencing: DECOUPLE v0.1.0 from RANGE's async permission. Ship machinery +
  11.2 + 11.3; the RANGE slice is a fast-follow. v0.1.0 states corpus status
  honestly.
- D1b Asset distribution: bundle a deterministic NEUTRAL slice (stable sort +
  first N), NOT the ~2M-row full file; full file referenced in the datasheet, and a
  reproducible committed slice script. No representativeness claim.
- D2 derive_geo stays GENERIC; the US-state extractor + reason split live in a
  DOCUMENTED tests/golden/_adapters.py (the reproduced Simpsonville configuration),
  not inside a primitive and not hidden as test magic.
- D3 CI: offline-only DEFAULT job; a workflow_dispatch HEAVY job for the 2 GB stack;
  a REQUIRED pre-tag release checklist gates the heavy paths at release cut.
- D4 The bundled PDF gets a sha256 + pure ingest_gate smoke in default CI; the full
  OCR + citation-anchor golden is docling-marked and REQUIRED in the pre-tag heavy
  run (it exists iff the Skokie PDF ships after vetting).
- Golden exactness: characterization over a fixed corpus -> EXACT assertions, with
  an explicit TOLERANCE band only for the spaCy PII tally; the distinct-agency count
  stays exact. No "~" in assertions.
- Release-critical adds: the corpus/public provenance datasheet, the RANGE
  permission record reference, the plugin-load/install smoke, and short v0.1.0
  release notes.
