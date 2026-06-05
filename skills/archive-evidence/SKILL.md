---
name: archive-evidence
description: Use when a user receives a FOIA artifact (a released record, a document dump, an export) and wants to record its provenance and establish a chain of custody before any processing. This skill hashes the file on receipt, obtains an RFC 3161 trusted timestamp proving that hash existed at a time, writes an append-only hash-chained custody log plus a provenance manifest, and routes a Librarian provenance note. Invoke it whenever the user wants to timestamp evidence, prove a file existed at a moment, record chain-of-custody on a received artifact, or build a defensible FOIA-receipt provenance record.
version: 0.1.0
---

archive-evidence records provenance at the moment an artifact is received. It calls ONE engine, scripts/evidence.py `archive_evidence`, which hashes the file, requests a trusted timestamp through an injected Timestamper, writes the local provenance artifacts, and returns a manifest. The SKILL orchestrates the Librarian provenance note from that manifest. evidence.py imports no Librarian; the script writes local files and the skill owns the note.

## 1. Receipt-first ordering

Hash before any processing. The receipt SHA-256 is the provenance anchor, so `archive_evidence` computes it first, on receipt, before redaction, conversion, or analysis touches the bytes. Run this skill at intake, not after a pipeline has already rewritten the file. The receipt clock is injected (the `now` argument), so the recorded received_at is the observation instant, not wall-clock drift inside the engine.

## 2. The RFC 3161 timestamp

The engine obtains an RFC 3161 trusted timestamp via the injected Timestamper (`Rfc3161Timestamper` for real use). Option A: the token's message imprint EQUALS the published receipt SHA-256, so a third party can verify with stock tooling. freeTSA (https://freetsa.org/tsr) is the documented default; the bundled freeTSA root verifies the reply on store (verify-on-store). The timestamper fails closed: a non-freeTSA URL with no supplied root does NOT opportunistically trust the reply (it keeps the token but reports status unverified, reason no_root_configured).

The degrade vocabulary is always a status plus a specific reason, never a silent bucket:

- verified -- a token was obtained AND verification passed.
- unverified -- a token was obtained but not verified or verification failed (reason: no_root_configured, verify_failed, imprint_mismatch).
- unavailable -- no token (reason: offline, http_error, bad_pki_status, timeout, empty_file).

reason is mandatory whenever status is not verified. The engine never fakes a gen_time and never crashes on a timestamp failure.

## 3. The custody log

`<receipt_sha256>.custody.jsonl` is append-only and hash-chained: each entry carries the SHA-256 of the previous entry (genesis = 64 zeros) plus its own entry hash. This is tamper-evident, NOT tamper-proof. The local chain detects an in-place edit of an entry, but it does not by itself detect a whole-log rewrite or a tail truncation. The RFC 3161 token is the external anchor for the receipt hash. Never describe this log as tamper-proof.

## 4. Output and the Librarian note

evidence.py writes LOCAL artifacts under out_dir, content-addressed by the receipt hash:

- `<sha256>.manifest.json` -- the provenance manifest (written last, atomically; it is the idempotency sentinel).
- `<sha256>.<serial>.tsr` -- the raw timestamp token sidecar (omitted when no token).
- `<sha256>.custody.jsonl` -- the append-only custody log.

The manifest carries no absolute local paths (filename is basename only). The SKILL then routes a Librarian provenance NOTE carrying only: the artifact filename, the receipt sha256, the timestamp status and gen_time, and a custody-log pointer. The raw token bytes and the full local paths stay LOCAL, mirroring the suite's local-vs-published split.

## 5. Honest limits

State these plainly; do not let the manifest imply more than it proves.

- A trusted timestamp attests that a HASH existed at a time. It does not prove authorship, that the artifact came from the named source, or that the file is unaltered relative to any other moment. It binds receipt-time, nothing more.
- Verification does not check revocation (no OCSP or CRL). That gap is recorded in the verification object (revocation_checked is always false), never hidden.
- The custody log is tamper-evident, not tamper-proof.
- The mtime check is a one-way alarm: a fired alarm is a lead (possible post-receipt modification); a silent alarm is NOT proof of integrity, because mtime is trivially forgeable.
- TLS to the TSA and RFC 3161 token trust are separate layers. An http:// TSA leaves the channel unauthenticated even though the signed token still holds.

## 6. Engine and downstream

- Engine module: scripts/evidence.py (`archive_evidence` plus the lazy `Rfc3161Timestamper` edge). Import-pure: it pulls no network or crypto library at import time.
- New dependency: rfc3161-client (Apache-2.0; sole dep is cryptography, already pinned). The live RFC 3161 round-trip is gated behind the `tsa` pytest marker.
- Bundled trust anchor: the freeTSA root at skills/archive-evidence/references/freetsa_cacert.pem, loaded module-relative and fingerprint-pinned in tests, never fetched live.
- No .mcp.json ships with this skill; the Librarian note is the only outward channel.
