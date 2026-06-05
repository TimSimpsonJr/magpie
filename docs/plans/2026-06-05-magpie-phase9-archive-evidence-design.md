# Magpie Phase 9 design -- archive-evidence (provenance + chain-of-custody)

Status: design (the WHY). Source of truth for the Phase 9 implementation plan
(`2026-06-05-magpie-phase9-archive-evidence.md`). Research gate:
`skills/archive-evidence/references/prior-art.md` (RFC 3161 facts + a live smoke).
ASCII-only (subagent-readable). Coding-task artifact: clarity over voice.

Brainstorm partner (Codex, autonomous stand-in) converged on this design after one
round that locked the schema and caught three risks now folded in: a receipt-hash
vs timestamp-read TOCTOU, output-path collisions, and local-write failure
semantics.

## 1. Purpose + scope

`archive-evidence` records, at the moment a FOIA artifact is RECEIVED, a provenance
record that a third party can later check: the artifact's SHA-256 (computed on
receipt, before any processing), an RFC 3161 trusted timestamp proving that hash
existed at a time, an append-only custody log, and a provenance manifest. It is the
suite's "FOIA-receipt provenance/custody" genuine-whitespace piece (design 10).

In scope (Tasks 9.1 + 9.2):
- `scripts/evidence.py` -- the engine (pure core + a lazy RFC 3161 TSA edge).
- `skills/archive-evidence/SKILL.md` -- the orchestration (+ the Librarian note).

DEFERRED (do NOT build; confirmed at the gate):
- Bellingcat Auto Archiver / WACZ web capture (design 5.1; layer 1->3).
- C2PA provenance + signed WACZ (design 12: immature / out of v1 scope).

## 2. Architecture: pure core + lazy TSA edge (mirrors the suite)

Same pure-core / engine-at-the-edge split as `pii_sweep` (lazy spaCy), `ingest`
(lazy docling), `redaction_check` (lazy x-ray), `citation.py` (pure, injected
clock):

- PURE CORE (stdlib + hashlib only; deterministic; the clock is INJECTED so the
  core is golden-testable): `sha256_file`, the custody-log append (hash-chained),
  the manifest assembly, the mtime one-way alarm, and the `archive_evidence`
  orchestration. Imports NO rfc3161-client at module top, NO requests, NO Librarian.
- TSA EDGE (lazy import of `rfc3161-client` + `requests`, integration-gated by a new
  `tsa` pytest marker): the `Rfc3161Timestamper`. The `Timestamper` PROTOCOL is
  injected into `archive_evidence`, exactly like `pii_sweep`'s injectable
  `PersonClassifier`: the golden suite passes a `FakeTimestamper` (canned result),
  so the whole core tests offline + fast + deterministic, and one live test hits a
  real TSA behind `-k tsa`.

`evidence.py` shares NO code with the Track-A analysis modules or the document path;
its only outward contract is the manifest (assembled here) that the SKILL routes to
the Librarian.

## 3. Receipt hash + the TOCTOU-safe timestamp interface (RISK 1, folded)

The receipt SHA-256 is the provenance anchor: `sha256_file(path)` streams the file
in 1 MiB chunks (memory-friendly for the pilot's 210 MB logs) and is computed FIRST,
before any other step.

TOCTOU: if the core hashes the file once and the timestamper later RE-READS it for
the Option-A imprint, a file changed in between would yield a valid token over bytes
that do NOT match the published receipt hash. The fix is a path-plus-expected-digest
interface and a post-hoc imprint assertion:

```
class Timestamper(Protocol):
    def timestamp_path(self, path: str, *, expected_sha256: str) -> TimestampResult: ...
```

The protocol NEVER accepts raw bytes (an attractive nuisance that invites whole-file
materialization in callers). The real `Rfc3161Timestamper.timestamp_path`:
1. reads the file and builds an Option-A request (imprint == SHA256(bytes read)),
2. POSTs to the TSA, decodes the response,
3. ASSERTS the token's messageImprint == `expected_sha256` (the already-computed
   receipt hash). If they differ, the file changed between reads -> return
   `status="unverified", reason="imprint_mismatch"` with a LOUD warning, never a
   normal success.

The assertion closes the TOCTOU window regardless of the second read: a mismatch is
always detected and flagged, never silently wrong.

## 4. RFC 3161 timestamp: Option A, verify-on-store, fail-closed

- Imprint mode: Option A ONLY (v1, locked at the gate). The token's messageImprint
  EQUALS the published receipt SHA-256, so a third party can verify with stock
  tooling (`openssl ts -verify -digest <published_sha256>` -- proven at the gate).
  Cost measured: +210 MB transient RAM / ~0.13 s on a 210 MiB file. No `imprint_mode`
  field in the manifest (hardcoded; if Option B is ever needed it arrives as an
  explicit field with both paths tested).
- Request: SHA-256, a random nonce (replay protection), cert_request=True (embed the
  TSA signing cert so the token is self-contained for the signature).
- Transport: POST via `requests` (TLS verified by certifi). TLS protects the CHANNEL
  only; token trust is independent (see verification). Record
  `transport: {scheme, http_status}` -- normalized, never raw library internals.
- TSA config: `tsa_url` is configurable; freeTSA (`https://freetsa.org/tsr`) is the
  documented default.
- Verify-on-store + trust anchor: the freeTSA CA cert is BUNDLED at
  `skills/archive-evidence/references/freetsa_cacert.pem` (loaded module-relative,
  mirroring how `ingest_gate` loads its bundled `common_words.txt`; NEVER fetched
  live) and is the zero-config default root. The caller may override with
  `root_cert_pem`. Verification semantics (read from the rfc3161-client source, prior
  art 3.1): GRANTED + chain-to-root validated at the token gen_time + nonce match +
  critical `id-kp-timeStamping` EKU + imprint match. FAIL CLOSED: if `tsa_url` is not
  freeTSA and no matching root is supplied, do NOT opportunistically trust the reply
  -- store the token but set `verification.verified=false`,
  `reason="no_root_configured"`.
- Empty artifact: an empty file cannot be RFC 3161 timestamped (the rfc3161-client
  builder rejects empty input). v1 still archives it locally (the receipt hash of an
  empty file is well-defined) with `timestamp.status="unavailable"`,
  `reason="empty_file"` -- a degrade, never a crash.

## 5. Provenance manifest schema (the test target)

A JSON object assembled by the core. Required shape (the schema test pins keys +
types; the `verification` object makes the honest limits visible, never a bare bool):

```
{
  "schema_name": "magpie-archive-evidence",
  "schema_version": "1",
  "tool": {"name": "magpie/archive-evidence", "version": "<plugin version>"},
  "artifact": {
    "filename": "<basename ONLY -- never an absolute local path>",
    "sha256": "<receipt hash, hex>",
    "size_bytes": <int>,
    "source_mtime": "<ISO 8601, tz-aware>",
    "received_at": "<ISO 8601, tz-aware -- the injected receipt clock>"
  },
  "timestamp": {
    "status": "verified" | "unverified" | "unavailable",
    "reason": "<MANDATORY whenever status != verified; specific>",
    "tsa_url": "<url>",
    "transport": {"scheme": "https"|"http", "http_status": <int|null>},
    "gen_time": "<ISO 8601 UTC, the TSA authoritative time | null>",
    "serial": <int|null>,
    "token_path": "<sidecar relative path | null>",
    "token_sha256": "<sha256 of the .tsr bytes | null>",
    "verification": {
      "granted": <bool>, "chain_ok": <bool>, "nonce_ok": <bool>,
      "eku_timestamping": <bool>, "imprint_match": <bool>,
      "revocation_checked": false,
      "verified": <bool>
    }
  },
  "custody_log_path": "<relative path>",
  "warnings": ["<e.g. possible post-receipt modification>", ...]
}
```

- `timestamp.reason` is MANDATORY whenever `status != "verified"` and is SPECIFIC:
  `unverified` distinguishes `no_root_configured` / `verify_failed` /
  `imprint_mismatch`; `unavailable` distinguishes `offline` / `http_error` /
  `bad_pki_status` / `timeout`. "unchecked" and "cryptographically bad" never
  collapse into one silent bucket.
- `token_sha256` integrity-pins the sidecar `.tsr`.
- No absolute local paths anywhere (portability + no FS-layout leak).

## 6. Custody log (append-only, hash-chained)

`<receipt_sha256>.custody.jsonl` -- one JSON object per line, append-only. Each entry:
`{seq, time, event, artifact_sha256, actor?, prev_entry_sha256, entry_sha256}`.
`prev_entry_sha256` is the SHA-256 of the previous entry's canonical JSON (genesis
prev = 64 zeros); `entry_sha256` chains the current entry. This is tamper-EVIDENT,
not tamper-PROOF: the local chain detects an in-place edit, but NOT a whole-log
rewrite or tail truncation unless the chain head is externally anchored. The RFC 3161
token is the external anchor for the artifact receipt hash; the SKILL doc states this
limit plainly and never calls the log tamper-proof.

## 7. Output layout + idempotency (RISK 2, folded)

Content-addressed by the receipt SHA-256 under `out_dir`:
- `<sha256>.manifest.json`
- `<sha256>.<serial>.tsr` (the token sidecar -- the serial keys it so re-timestamps
  or a second TSA never collide; omitted when no token)
- `<sha256>.custody.jsonl`

`archive_evidence(..., on_exists="error")`: if a manifest for this receipt hash
already exists, the DEFAULT is to refuse (raise / return a clear "already archived"
result) -- re-archival is an explicit opt-in (`on_exists="append_event"` appends a
custody event and refreshes), never a silent overwrite.

## 8. Failure semantics + degrade vocabulary (RISK 3, folded)

Two failure classes, deliberately asymmetric:
- TSA failure (network down, HTTP error, bad PKIStatus, timeout, verify failure) ->
  SOFT degrade: the timestamp object records `status` in {unverified, unavailable}
  with a specific `reason`; the archive still succeeds (the receipt hash + custody +
  manifest are the load-bearing local provenance). Never fake a `gen_time`, never
  crash.
- Local persistence failure (custody append, sidecar write, manifest write) -> HARD
  FAIL: raise. We cannot claim an archive we did not persist.

Ordering in `archive_evidence` (the manifest is the idempotency sentinel, so it is
the ATOMIC FINAL commit point -- a mid-archive failure must never strand a sentinel
that blocks a clean retry):
1. Snapshot `src.stat()` AND `sha256_file` at receipt (unreadable -> hard fail). The
   stat snapshot feeds size_bytes/source_mtime so they describe the hashed bytes
   (the artifact-metadata half of the TOCTOU guard, not just the imprint).
2. Idempotency check: if `<sha256>.manifest.json` exists -> the on_exists policy.
3. mtime one-way alarm (append a warning; never fails).
4. `timestamper.timestamp_path(path, expected_sha256=receipt_hash)` (soft degrade).
5. Write the token sidecar if a token exists (write failure -> hard fail).
6. Append the "archived" custody event (append failure -> hard fail).
7. Write the manifest to a temp file and `os.replace` it into the final name -- the
   ATOMIC FINAL commit point (write failure -> hard fail). Because the sentinel
   appears only on success, a failed attempt leaves no manifest and a retry is clean.

`status` vocabulary: `verified` (token obtained AND verification passed);
`unverified` (token obtained, not verified or verification failed -- with reason);
`unavailable` (no token -- with reason).

## 9. The mtime one-way alarm

`check_mtime_after_receipt(path, received_at)`: compares the file's filesystem mtime
to the receipt observation instant, tz-normalized. If mtime > received_at, append a
warning worded "possible post-receipt modification". The alarm FIRING is a lead; the
alarm NOT firing is NOT proof of integrity (mtime is trivially forgeable). Never
phrase the silent case as "unmodified".

## 10. The archive-evidence SKILL.md (orchestration)

Like the rest of the suite, the SCRIPT assembles the manifest + writes the LOCAL
artifacts; the SKILL orchestrates the Librarian output. `evidence.py` imports no
Librarian. The skill:
1. Calls `archive_evidence(path, timestamper=Rfc3161Timestamper(...), out_dir=...)`
   on receipt, before any processing.
2. Routes a provenance NOTE to the Librarian: the artifact filename + receipt sha256
   + timestamp status/gen_time + custody-log pointer (counts/anchor only; the raw
   token + full local paths stay LOCAL, mirroring the suite's local-vs-published
   split).
3. Documents the honest limits (section 12) plainly: what a timestamp does and does
   NOT prove; verification gaps; the custody log is tamper-evident not tamper-proof;
   TLS vs token trust are separate layers.

## 11. Testing

Pure golden suite (no network, no model, via `FakeTimestamper`):
- `sha256_file` streamed hash correctness (vs hashlib over the whole file).
- Manifest schema: required keys + types pinned; `reason` present whenever status
  != verified; no absolute paths; `verification` object shape.
- mtime warning fires when mtime > received_at, silent otherwise; tz-normalized.
- Custody chain: genesis prev = zeros; each entry chains; an edited middle entry
  breaks the chain (a verify helper detects it).
- Degrade: a FakeTimestamper returning unavailable/unverified still produces a valid
  manifest + the archive succeeds; reason is specific.
- imprint_mismatch: a FakeTimestamper whose token imprint != expected_sha256 yields
  status unverified / reason imprint_mismatch + a warning (the TOCTOU guard).
- Idempotency: a second `archive_evidence` over the same content with the default
  `on_exists="error"` refuses; `append_event` appends a custody entry.
- Local-write hard-fail: an unwritable out_dir raises (does not fake success).
- Fail-closed: a non-freeTSA tsa_url with no supplied root -> verified=false /
  no_root_configured.

Live tier (`@pytest.mark.tsa`, one test, `-k tsa`): a real freeTSA round-trip ->
status verified, gen_time present, the bundled-root verification passes, and the
token cross-verifies (optional openssl assertion if available).

Bundled-cert test: pin `freetsa_cacert.pem` by SHA-256 fingerprint (catch a silent
swap; no live fetch).

Skill smoke (PyYAML, mirrors `test_investigate_skill`): SKILL.md frontmatter
name/description/version; body documents the receipt-first ordering, the manifest +
custody artifacts, the Librarian note split, the degrade vocabulary, and the honest
limits.

## 12. Honest limits (documented, not hidden)

- A trusted timestamp attests that a HASH existed at a time. It does NOT prove
  authorship, that the artifact came from the named source, or that the file is
  unaltered relative to any other moment. It binds receipt-time, nothing more.
- Verification does NOT check revocation (no OCSP / CRL) and pins policy only if
  configured (prior art 3.1). Recorded in `verification`, never hidden.
- The custody log is tamper-EVIDENT (local chain), not tamper-PROOF.
- mtime is forgeable; the alarm is one-way.
- TLS to the TSA and RFC 3161 token verification are separate trust layers; an
  `http://` TSA leaves the channel unauthenticated though the signed token still
  holds.

## 13. Dependencies

- NEW: `rfc3161-client==1.0.6` (Apache-2.0). Sole dep is `cryptography` (already
  pinned 48.0.0); prebuilt cp39-abi3 win_amd64 wheel; does no network. Added to
  `requirements-dev.txt`. New `tsa` pytest marker in `pyproject.toml` (mirrors
  spacy/docling/xray).
- `requests` (already present) for the POST; `certifi` (present) for TLS.
- Bundled asset: `skills/archive-evidence/references/freetsa_cacert.pem` (freeTSA CA,
  valid to 2040), fingerprint-pinned in tests.

## 14. Out of scope / deferred

Auto Archiver / WACZ web capture; C2PA provenance; signed WACZ; external anchoring of
custody-log heads; multi-TSA redundancy. None are load-bearing for a credible minimal
Phase 9 -- the load-bearing pieces are the TOCTOU guard, explicit trust semantics,
and idempotent output.
