# Phase 9 research gate: archive-evidence (provenance + chain-of-custody)

Verified-facts-only brief (the Tier-2 research gate). The algorithm, schema, and
test plan live in the Phase 9 plan/design under `docs/plans/`. This file records
what was VERIFIED at the gate (primary sources + a live end-to-end smoke on this
box), so the implementers build against a checked API, not memory.

ASCII-only by rule (subagent-readable). No em-dashes / smart quotes.

GATE STATUS: Codex research-gate review = PROCEED-WITH-FIXES (0 critical, 5
important, 3 nice). All findings folded below (sec 3.1 verification semantics, sec
6 Option-A lock + measured cost, sec 4 nonce note, sec 9 mtime one-way-alarm +
transport-trust, sec 10.1 custody-log honest limit). Threaded session
019e95ea-d5d9-72f0-87db-e1bbd50a4c42.

## 0. Scope

In scope (Task 9.1 + 9.2, "minimal"):
- SHA-256 on RECEIPT (before any processing -- the provenance anchor).
- An RFC 3161 trusted timestamp from a free TSA.
- An append-only custody log.
- A provenance manifest (JSON) written via Librarian.
- The `archive-evidence` SKILL.md.

DEFERRED (explicit, do NOT build in Phase 9):
- Bellingcat Auto Archiver / WACZ web capture (design 5.1, 10; "1->3").
- C2PA provenance + signed WACZ (design 12: deferred, immature / out of v1 scope).

## 1. RFC 3161 Time-Stamp Protocol (verified: RFC 3161, OpenSSL docs, freeTSA)

Trusted timestamping binds a hash to a time, signed by a Time-Stamp Authority
(TSA). The client never sends the file -- only a hash (the messageImprint).

Flow:
1. Compute a digest of the data (we use SHA-256, the receipt hash).
2. Build a TimeStampReq (DER, a.k.a. `.tsq`): messageImprint{hashAlgorithm OID,
   hashedMessage}, optional random nonce, optional certReq flag (ask the TSA to
   embed its signing cert in the token).
3. POST the request over HTTP with header
   `Content-Type: application/timestamp-query`; the TSA replies with
   `Content-Type: application/timestamp-reply` -- a TimeStampResp (DER, `.tsr`)
   carrying a PKIStatus + a TimeStampToken (a CMS SignedData) whose TSTInfo holds
   {genTime, serialNumber, policy, messageImprint (echoes your digest), accuracy,
   nonce, tsa name}.
4. Verify: check the token's CMS signature against the TSA cert chain, that the
   echoed messageImprint matches your digest, and (if used) the nonce matches.

genTime is the authoritative trusted time (UTC). It is the TSA's time, NOT ours.

## 2. Free, no-auth TSAs (verified)

| TSA | POST endpoint | Verify certs | Notes |
|-----|---------------|--------------|-------|
| freeTSA.org | `https://freetsa.org/tsr` | `https://freetsa.org/files/cacert.pem`, `https://freetsa.org/files/tsa.crt` | Free, no logs, no auth. Cert valid to 2040 (P-384). SHA1/224/256/384/512. "do not abuse" (no hard rate limit). PRIMARY pick: downloadable certs make verification reproducible. |
| DigiCert | `http://timestamp.digicert.com` | DigiCert public roots | Free, no-auth, code-signing TSA. No single cacert download page. |
| Entrust | `http://timestamp.entrust.net/TSS/RFC3161sha2TS` | Entrust public roots | Free, no-auth. |

freeTSA is the recommended default because the CA + TSA certs are published at
stable URLs, so verification (and the test fixture) is fully reproducible.

## 3. Mechanism A -- Python: `rfc3161-client` (Trail of Bits) [RECOMMENDED]

- License: Apache-2.0 (matches Magpie's permissive-only default stack, design 8).
- Version 1.0.6 (2026-04-08); Requires-Python >=3.9; used by the Sigstore Python
  client (battle-tested).
- Install footprint on THIS venv: adds ONLY `rfc3161-client`. Its sole runtime dep
  is `cryptography` (>=43), ALREADY present (48.0.0). The wheel is a prebuilt
  `cp39-abi3-win_amd64` native wheel (Rust/PyO3 under the hood) -- NO Rust
  toolchain needed to install. Verified: `pip install rfc3161-client` pulled
  nothing else.
- Does NO network. It builds request bytes and parses/verifies response bytes;
  HTTP transport is the caller's job (we use `requests`, already present). This is
  the architectural win: network stays at OUR edge, the build/parse/verify core is
  in-process and golden-testable.

Verified API (by `inspect` on the installed 1.0.6):
```
from rfc3161_client import (
    HashAlgorithm,                 # enum: SHA256, SHA512 (pass the ENUM, not a
                                   #   cryptography hashes object -- TypeError else)
    TimestampRequestBuilder,
    decode_timestamp_response,
    VerifierBuilder, Verifier, VerificationError,
)

req = (TimestampRequestBuilder()
       .data(b"...")                       # the bytes to imprint (lib hashes them)
       .hash_algorithm(HashAlgorithm.SHA256)
       .nonce(nonce=True)                  # random nonce (replay protection)
       .cert_request(cert_request=True)    # embed TSA signing cert in the token
       .build())                           # -> TimeStampRequest
der = req.as_bytes()                       # POST this (application/timestamp-query)

tsr = decode_timestamp_response(resp_bytes)   # -> TimeStampResponse
tsr.status                                 # PKIStatus int (0 == GRANTED)
tsr.status_string
info = tsr.tst_info                        # TimeStampTokenInfo
info.gen_time                              # tz-aware datetime in UTC (authoritative)
info.serial_number
info.message_imprint                       # echoes our digest
token_der = tsr.time_stamp_token()         # raw DER token bytes (persist this)
full_der = tsr.as_bytes()                  # full response (openssl ts -verify -in)

# Verify (raises VerificationError on failure):
vb = VerifierBuilder.from_request(req)     # seeds nonce + imprint checks from req
vb = vb.add_root_certificate(root_cert)    # cryptography.x509.Certificate
vb = vb.tsa_certificate(tsa_leaf_cert)     # optional; token already embeds it
verifier = vb.build()
verifier.verify_message(tsr, b"...")       # RE-HASHES the message, compares imprint
verifier.verify(tsr, hashed_message=dgst)  # compares dgst DIRECTLY to the imprint
```

CRITICAL API distinction (proven in the smoke, sec 5): `verify_message(resp, X)`
hashes X for you; `verify(resp, hashed_message=Y)` expects Y to ALREADY equal the
imprint. So if the request was built with `.data(X)` (imprint = SHA256(X)), verify
with `verify_message(resp, X)` OR `verify(resp, hashed_message=SHA256(X))`.

## 3.1 What `verified` MEANS in v1 (read from the lib source -- the honest stance)

A bare `verified: true` is not enough (per the gate review). Read from
`rfc3161_client/verify.py` (1.0.6), `Verifier.verify()` enforces, IN ORDER:
1. PKIStatus == GRANTED (it REJECTS even GRANTED_WITH_MODS -- strict).
2. Chain-to-configured-root, with the PKCS7 verification time set to the token's
   `gen_time` (certs need be valid at TIMESTAMP time, NOT now -- so a token stays
   verifiable after the TSA cert later expires). Requires >=1 root cert.
3. Nonce match (when a nonce is set; `VerifierBuilder.from_request(req)` seeds it).
4. Policy-OID match (ONLY if a policy_id is configured; else not checked).
5. Leaf-cert checks: identifies the leaf via the signature-covered SignerInfo,
   then REQUIRES a CRITICAL ExtendedKeyUsage extension carrying
   `id-kp-timeStamping` (RFC 3161 2.3). Optional ESSCertID / common-name pins.
6. messageImprint == the supplied digest.

So in v1 we DEFINE `verified` as: GRANTED + chain-to-our-configured-root@gen_time
+ nonce-match + critical timestamping-EKU + imprint-match. HONEST LIMITS to record
in the manifest (NOT a bare bool): revocation is NOT checked (no OCSP / CRL), and
policy-OID is not pinned unless configured. The manifest's `verification` is a
STRUCTURED object {granted, chain_ok, nonce_ok, eku_timestamping, imprint_match,
revocation_checked: false} + a `verified` rollup, so the gaps are visible, never
hidden (the suite's "honest limits, documented not hidden" stance, design 7).

## 4. Mechanism B -- OpenSSL `ts` CLI (available; the no-Python-dep cross-check)

- THIS box: OpenSSL 3.5.4 (Git for Windows, `C:\Program Files\Git\mingw64\bin\openssl.exe`)
  HAS the `ts` subcommand compiled in (probed: `openssl ts` -> "Must give one of
  -query, -reply, or -verify"). `curl.exe` is built into Windows (system32).
- Commands (verified against OpenSSL docs + freeTSA + a live verify in sec 5):
  ```
  # build request from a precomputed digest (no config file needed for -query).
  # PRODUCTION sends a random nonce (replay protection); the primary
  # rfc3161-client path always does. `-no_nonce` is SMOKE-ONLY -- it was used in
  # sec 5 so an `openssl ts -verify -digest` cross-check (which does NOT compare a
  # nonce; only `-queryfile` does) would not trip on the absent nonce.
  openssl ts -query -digest <hex_sha256> -sha256 -cert -out req.tsq
  # POST:
  curl <TSA_URL> -H "Content-Type: application/timestamp-query" \
       --data-binary @req.tsq -o resp.tsr
  # human-readable:
  openssl ts -reply -in resp.tsr -text
  # verify (against the file's digest):
  openssl ts -verify -digest <hex_sha256> -in resp.tsr -CAfile cacert.pem -untrusted tsa.crt
  ```
- `openssl ts` does NOT do HTTP transport (must use curl/requests).
- PORTABILITY CAVEAT: the `ts` subcommand is NOT in LibreSSL (macOS system
  `/usr/bin/openssl`) and is disabled in some distro builds. Shelling to
  `openssl ts` is therefore non-portable for a cross-platform journalist tool. It
  works here, but it is the FALLBACK / cross-verification path, not the primary.

## 5. LIVE END-TO-END SMOKE (decisive evidence, run on this box 2026-06-05)

A full round-trip against freeTSA (`https://freetsa.org/tsr`) from the venv:

- HTTP 200, `Content-Type: application/timestamp-reply`, PKIStatus 0 (GRANTED).
- genTime returned as a tz-aware UTC datetime (e.g. `2026-06-05 18:13:48+00:00`).
- A 4634-byte DER token retrieved (cert_request=True embeds the signing cert).
- Option A (build with `.data(file_content)`; imprint == receipt SHA-256):
  `verify(tsr, hashed_message=receipt_sha256)` -> True.
- Option B (build with `.data(receipt_digest_bytes)`; imprint == SHA256(digest)):
  `verify_message(tsr, receipt_digest)` -> True.
- CROSS-TOOL: writing the Option-A response to `resp.tsr` and running
  `openssl ts -verify -digest <receipt_sha256> -in resp.tsr -CAfile cacert.pem
  -untrusted tsa.crt` returned exit 0 / "Verification: OK". (A benign stderr note
  "tsa.crt ... is not a CA cert" accompanies the OK -- it is the leaf, expected.)

Conclusion: the rfc3161-client path WORKS end-to-end, and a token it obtains is
INDEPENDENTLY verifiable by standard OpenSSL against the PUBLISHED receipt hash
(Option A). That cross-tool verifiability is the credibility argument for the
provenance use case (a third party can verify our manifest's SHA-256 against the
token with stock tooling).

## 6. Imprint mode: v1 is LOCKED to Option A (decided at the gate)

Two ways to build the imprint were proven (sec 5):
- Option A -- timestamp the FILE CONTENT (`.data(file_bytes)`): the token's
  messageImprint EQUALS the receipt SHA-256 we publish. Directly, independently
  verifiable (incl. by `openssl ts -verify -digest <published_hash>`); ONE hash
  value, ONE verifier semantic.
- Option B -- timestamp the RECEIPT DIGEST (`.data(receipt_sha256_bytes)`): imprint
  = SHA256(receipt_digest), a hash-of-a-hash; an external verifier must know to
  re-hash the published digest. File-size-independent but breaks drop-in interop.

DECISION (gate review IMP-2): v1 ships Option A ONLY. A size-based fallback to B
would create two verifier semantics, so it is OUT. If B is ever needed, it must
arrive as an EXPLICIT `imprint_mode` field in the manifest with both paths tested
-- not an implicit size switch.

MEASURED cost of Option A's full materialization (gate review IMP-3; benchmarked on
this box against a 210 MiB file == the pilot's largest):
- streamed receipt hash (1 MiB chunks, what we already do): ~0.15 s, low memory.
- Option A request build (`open().read()` + `.data()` + `.build()`): ~0.13 s, peak
  Python allocation +210.0 MB (EXACTLY the file size -- no extra copies; the Rust
  side hashes the buffer in place). DER request is 69 bytes.
So Option A costs ~210 MB TRANSIENT RAM on the worst-case pilot file (freed
immediately). Acceptable for laptop-local FOIA scale. (For hypothetical >1 GB
inputs the OpenSSL `-digest` path builds an Option-A imprint from the streamed
digest WITHOUT materializing -- a documented future large-file escape hatch, NOT a
v1 code path.)

## 7. Architecture fit (pure-core / network-at-the-edge; matches the suite)

Mirror pii_sweep (lazy spaCy), ingest (lazy docling), redaction_check (lazy x-ray):
- PURE CORE (stdlib + hashlib; golden-testable, deterministic, clock INJECTED like
  citation.py): streamed `sha256_file` (hash-on-receipt); the provenance manifest
  assembly (dataclass -> JSON); the append-only custody-log append logic; the
  mtime-before-hash warning.
- TSA EDGE (lazy import of rfc3161-client + requests; integration-gated): build
  request, POST, decode, (optionally) verify, return {genTime, serial, token,
  tsa_url, verified}. OFFLINE / no-TSA / HTTP-error / bad-status -> record
  `timestamp: {status: "unavailable", reason: ...}` HONESTLY; never fake a time,
  never crash (degrade-don't-crash, like x-ray CheckUnavailable and the
  Tesseract-gated OCRmyPDF seam).
- Inject the timestamper (a `Timestamper` protocol) like pii_sweep injects the
  PersonClassifier and ingest_gate injects the wordlist: the pure suite passes a
  FAKE timestamper (returns a canned token/genTime) so it stays offline + fast +
  deterministic. A new pytest marker (propose `tsa`, mirroring spacy/docling/xray)
  guards the ONE live-TSA integration test.

## 8. Environment facts (verified on this box)

- venv ALREADY has: `cryptography` 48.0.0, `requests` 2.34.2, `certifi`
  2026.5.20, `urllib3` 2.7.0. So Mechanism A adds only `rfc3161-client`.
- `curl.exe` built into Windows (`C:\Windows\system32\curl.exe`).
- OpenSSL 3.5.4 with `ts` at the Git path above.
- requests verifies TLS via certifi by default (freeTSA.org has a valid TLS cert).

## 9. Clock / determinism discipline

- Receipt time (what WE record as "received_at" / "computed_at") is a real
  wall-clock read, but make it INJECTABLE (a `now`/clock parameter) so tests are
  deterministic -- same discipline as citation.py's injected timestamp.
- The TSA genTime is the authoritative external time; we store it verbatim.
- mtime-before-hash check is a ONE-WAY ALARM (gate review NICE): compare the file's
  filesystem mtime to the hash-START instant (the receipt observation), normalizing
  tz + granularity (mtime is naive-local with coarse resolution; the receipt time
  is tz-aware). If mtime is AFTER the receipt observation, emit a warning worded as
  "possible post-receipt modification". The alarm FIRING is a lead; the alarm NOT
  firing is NOT proof of integrity (mtime is trivially forgeable). Never phrase the
  silent case as "unmodified".
- Transport trust is SEPARATE from token trust (gate review NICE): TLS only
  protects the POST channel; the timestamp's trust comes from RFC 3161 signature
  verification against the configured root (sec 3.1), independent of transport. A
  signed token over plain `http://` is still cryptographically valid, but the
  channel is unauthenticated -- so record `transport: "http"|"https"` in the
  manifest to keep the posture honest. freeTSA is https; DigiCert/Entrust are http.

## 10. Decisions + remaining open questions

RESOLVED at the gate (gate review folded):
- R1. Imprint mode: v1 = Option A ONLY (sec 6). No size fallback.
- R2. `verified` semantics: the structured object in sec 3.1; revocation is an
  explicit honest limit.
- R3. Mechanism: rfc3161-client primary (Apache-2.0, only-dep-is-cryptography,
  win wheel, no-network/edge-friendly); OpenSSL `ts` is the cross-check / future
  large-file escape hatch, NOT a v1 code path.

OPEN for brainstorming / design:
1. Custody log format: append-only JSONL of {event, artifact_sha256, time, actor,
   prev_entry_sha256, ...}. Lean: hash-CHAINED JSONL. HONEST LIMIT to state
   (gate review IMP-5): a hash chain is LOCAL tamper-evidence only -- it detects an
   in-place edit, but NOT a whole-log rewrite or tail truncation unless the chain
   HEAD is anchored externally. The external anchor IS the RFC 3161 timestamp
   (timestamp the artifact, and optionally the log head), so do not overstate
   "chain-of-custody"; word it as tamper-EVIDENT, not tamper-PROOF.
2. Provenance manifest schema (the test target): required keys/types; where the
   token bytes live (inline base64 vs a sidecar `.tsr` path); the `verification`
   sub-object shape (sec 3.1); `transport` + `imprint_mode` fields.
3. Librarian write path: which Librarian API writes the manifest note (reuse the
   suite's hub-and-spoke output); portable-Markdown vs vault. (Like the rest of the
   suite, the SCRIPT assembles the manifest; the SKILL orchestrates the Librarian
   note -- evidence.py imports no Librarian.)
4. Verify-on-store: verify the token immediately when a TSA root cert is
   configured/bundled (catch a forged/garbled reply at receipt), else
   store-without-verify and flag honestly. Bundling freeTSA's cacert.pem (valid to
   2040, ~1-2 KB) makes verify reproducible but couples to one TSA; making the TSA
   + root configurable avoids the coupling. Lean: configurable TSA, freeTSA the
   documented default, verify-on-store whenever a root is available.
5. Offline degrade contract wording + the manifest `timestamp.status` vocabulary
   (e.g. verified / unverified / unavailable).

## Sources

- RFC 3161 (rfc-editor.org/rfc/rfc3161.html); OpenSSL `openssl-ts` man page
  (docs.openssl.org/3.2/man1/openssl-ts/).
- freeTSA.org (endpoint + cert URLs + algorithms + validity).
- rfc3161-client: github.com/trailofbits/rfc3161-client, pypi.org/project/rfc3161-client/
  (Apache-2.0, 1.0.6, Requires-Python >=3.9, cp39-abi3 win_amd64 wheel); API
  verified by local introspection of the installed package.
- rfc3161ng (github.com/trbs/rfc3161ng): LGPL, pyasn1-based, RemoteTimestamper does
  its own network POST -- considered and NOT chosen (copyleft + older ASN.1 stack +
  network buried in the lib).
- Live smoke on this box (sec 5): freeTSA round-trip + rfc3161-client verify +
  openssl ts -verify cross-check, all green.
