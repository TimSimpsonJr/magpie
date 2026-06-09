"""Magpie Phase 9 -- the evidence provenance + chain-of-custody engine.

PURE CORE (this file's top half): stdlib + hashlib only -- sha256_file, the
hash-chained custody log, the manifest assembly, the mtime one-way alarm, and the
archive_evidence orchestration. Deterministic: the receipt clock is INJECTED (the
`now` parameter), like citation.py, so the core is golden-testable.

TSA EDGE (Rfc3161Timestamper, lower half): lazily imports rfc3161-client + requests
INSIDE its method (mirrors pii_sweep's lazy spaCy edge), so importing this module
stays network-free. The Timestamper PROTOCOL is injected into archive_evidence; the
golden suite passes a FakeTimestamper.

Imports NO Librarian (the SKILL orchestrates the note). Source of truth:
docs/plans/2026-06-05-magpie-phase9-archive-evidence-design.md.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

SCHEMA_NAME = "magpie-archive-evidence"
SCHEMA_VERSION = "1"
TOOL_NAME = "magpie/archive-evidence"

FREETSA_URL = "https://freetsa.org/tsr"
_GENESIS_PREV = "0" * 64           # custody chain genesis prev-hash
_CHUNK = 1024 * 1024               # 1 MiB streaming read

# Bundled freeTSA Root CA -- the zero-config default verification root. Loaded
# module-relative (mirrors ingest_gate's bundled common_words.txt), NEVER fetched
# live. Pinned by DER fingerprint in tests.
_DEFAULT_ROOT_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "archive-evidence" / "references" / "freetsa_cacert.pem"
)
_DEFAULT_ROOT_CACHE: Optional[bytes] = None


class ArchiveExistsError(Exception):
    """Raised by archive_evidence when a manifest for this content already exists
    and on_exists == 'error' (never silently overwrite a provenance record)."""


class CustodyAppendError(OSError):
    """Raised when the manifest COMMITTED but the post-commit 'archived' custody
    append failed. The archive's authoritative record (the manifest) EXISTS at
    `manifest_path`; only the custody event is missing.

    Recovery (honest limit): if the failure was transient and the custody log is
    WRITABLE and WELL-FORMED, recover WITHOUT re-archiving by calling
    archive_evidence(..., on_exists='append_event'), which appends a custody event to
    the already-committed manifest. If the failure was a CORRUPT existing custody log
    (a malformed last line), on_exists='append_event' re-reads that same log and fails
    the same way -- the log must be repaired manually first (a repair path is out of
    scope for v1). The manifest itself is intact either way."""
    def __init__(self, manifest_path, original):
        super().__init__("manifest committed at %s but custody append failed: %s"
                         % (manifest_path, original))
        self.manifest_path = manifest_path
        self.original = original


def load_default_root_cert_pem() -> bytes:
    """The bundled freeTSA Root CA PEM bytes (read once, cached)."""
    global _DEFAULT_ROOT_CACHE
    if _DEFAULT_ROOT_CACHE is None:
        _DEFAULT_ROOT_CACHE = _DEFAULT_ROOT_PATH.read_bytes()
    return _DEFAULT_ROOT_CACHE


def sha256_file(path, *, chunk_size: int = _CHUNK) -> str:
    """Streamed SHA-256 hex of a file -- the receipt hash (the provenance anchor).

    Streamed in chunk_size blocks so a 210 MB FOIA log hashes in low memory.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Timestamp result + injectable protocol
# --------------------------------------------------------------------------- #
@dataclass
class TimestampResult:
    """The outcome of a TSA round-trip. token_der is the raw .tsr bytes (written to
    a sidecar; NEVER embedded in the manifest JSON)."""
    status: str                                  # verified | unverified | unavailable
    reason: Optional[str] = None                 # MANDATORY when status != verified
    tsa_url: Optional[str] = None
    transport: Optional[Dict[str, Any]] = None   # {scheme, http_status}
    gen_time: Optional[str] = None               # ISO 8601 UTC (authoritative)
    serial: Optional[int] = None
    token_der: Optional[bytes] = None
    verification: Optional[Dict[str, Any]] = None


class Timestamper(Protocol):
    """Injected into archive_evidence. Path-plus-expected-digest ONLY (never raw
    bytes -- an attractive nuisance that invites whole-file materialization in
    callers). The implementation MUST assert the token imprint == expected_sha256
    (the TOCTOU guard)."""
    def timestamp_path(self, path: str, *, expected_sha256: str) -> TimestampResult: ...


@dataclass
class FakeTimestamper:
    """Golden-suite stand-in (like pii_sweep's fake PersonClassifier). Returns a
    canned result; records the (path, expected_sha256) it was called with."""
    result: TimestampResult
    calls: List = field(default_factory=list)

    def timestamp_path(self, path: str, *, expected_sha256: str) -> TimestampResult:
        self.calls.append((path, expected_sha256))
        return self.result


# --------------------------------------------------------------------------- #
# mtime one-way alarm
# --------------------------------------------------------------------------- #
def check_mtime_after_receipt(path, received_at: datetime) -> Optional[str]:
    """One-way alarm: warn if the file's mtime is AFTER the receipt instant.

    Firing is a lead ("possible post-receipt modification"); NOT firing is NOT proof
    of integrity (mtime is trivially forgeable). tz-normalized to UTC.
    """
    mtime = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
    ra = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
    if mtime > ra:
        return ("possible post-receipt modification (mtime %s > received_at %s)"
                % (mtime.isoformat(), ra.isoformat()))
    return None


# --------------------------------------------------------------------------- #
# Custody log -- append-only, hash-chained (tamper-EVIDENT, not tamper-proof)
# --------------------------------------------------------------------------- #
def _canonical(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def append_custody_event(log_path, event: str, *, now: datetime,
                         artifact_sha256: str, actor: Optional[str] = None) -> Dict[str, Any]:
    """Append one hash-chained entry. Each entry's entry_sha256 = sha256 of its
    canonical core (everything but entry_sha256); prev_entry_sha256 chains to the
    prior line (genesis = 64 zeros). Detects in-place edits, not whole-log rewrite
    (the RFC 3161 token is the external anchor)."""
    p = Path(log_path)
    prev = _GENESIS_PREV
    seq = 0
    if p.exists():
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        seq = len(lines)
        if lines:
            prev = json.loads(lines[-1])["entry_sha256"]
    core = {"seq": seq, "time": now.isoformat(), "event": event,
            "artifact_sha256": artifact_sha256, "actor": actor,
            "prev_entry_sha256": prev}
    entry = dict(core)
    entry["entry_sha256"] = hashlib.sha256(_canonical(core).encode("utf-8")).hexdigest()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(_canonical(entry) + "\n")
    return entry


def verify_custody_chain(log_path) -> bool:
    """True iff every entry's recomputed hash matches and prev-links are intact."""
    p = Path(log_path)
    if not p.exists():
        return False
    prev = _GENESIS_PREV
    try:
        for i, ln in enumerate(x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()):
            d = json.loads(ln)
            if not isinstance(d, dict):              # valid JSON but not an object -> reject
                return False
            claimed = d.pop("entry_sha256", None)
            if hashlib.sha256(_canonical(d).encode("utf-8")).hexdigest() != claimed:
                return False
            if d.get("seq") != i or d.get("prev_entry_sha256") != prev:
                return False
            prev = claimed
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
        # malformed/truncated/tampered line -> fails verification (never crashes)
        return False
    return True


# --------------------------------------------------------------------------- #
# Manifest + orchestration
# --------------------------------------------------------------------------- #
@dataclass
class EvidenceManifest:
    schema_name: str
    schema_version: str
    tool: Dict[str, Any]
    artifact: Dict[str, Any]
    timestamp: Dict[str, Any]
    custody_log_path: str
    warnings: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _full_verification(**overrides: Any) -> Dict[str, Any]:
    """ALWAYS the complete 7-key verification object (design 5); deterministic on
    every branch. revocation_checked is always False (honest limit: no OCSP/CRL).
    PURE -- lives in the core so _timestamp_block (core) and the TSA edge both share
    it, and every manifest carries the full object even on an unavailable timestamp."""
    base = {"granted": False, "chain_ok": False, "nonce_ok": False,
            "eku_timestamping": False, "imprint_match": False,
            "revocation_checked": False, "verified": False}
    base.update(overrides)
    return base


def _timestamp_block(tr: TimestampResult, token_path: Optional[str],
                     token_sha256: Optional[str]) -> Dict[str, Any]:
    # NORMALIZE verification here (the choke point): every manifest carries the full
    # 7-key object, even an unavailable timestamp (all-False) -- never null (design 5).
    return {"status": tr.status, "reason": tr.reason, "tsa_url": tr.tsa_url,
            "transport": tr.transport, "gen_time": tr.gen_time, "serial": tr.serial,
            "token_path": token_path, "token_sha256": token_sha256,
            "verification": _full_verification(**(tr.verification or {}))}


def archive_evidence(path, *, timestamper: Timestamper, out_dir, now: datetime,
                     tool_version: str = "0.3.0", actor: Optional[str] = None,
                     on_exists: str = "error") -> EvidenceManifest:
    """Record provenance for one artifact ON RECEIPT. Ordering + failure classes per
    design 8: TSA failure SOFT-degrades; any LOCAL write failure HARD-fails. The
    manifest is the idempotency sentinel and is the ATOMIC FINAL commit point (temp +
    os.replace), so a mid-archive failure never strands a sentinel that blocks a clean
    retry.

    on_exists: 'error' (default) refuses to overwrite an existing manifest for this
    content; 'append_event' appends a re-receipt custody event and returns the
    existing manifest.
    """
    src = Path(path)
    out = Path(out_dir)

    stat = src.stat()                                # snapshot metadata AT RECEIPT...
    receipt = sha256_file(src)                       # 1. ...then hash (hard fail if unreadable)
    manifest_path = out / (receipt + ".manifest.json")
    custody_name = receipt + ".custody.jsonl"

    if manifest_path.exists():                       # 2. idempotency sentinel
        if on_exists == "error":
            raise ArchiveExistsError("already archived: %s" % manifest_path.name)
        if on_exists == "append_event":
            append_custody_event(out / custody_name, "re-received", now=now,
                                 artifact_sha256=receipt, actor=actor)
            return _load_manifest(manifest_path)
        raise ValueError("unknown on_exists policy: %r" % on_exists)

    out.mkdir(parents=True, exist_ok=True)           # hard fail if parent is a file

    warnings: List[str] = []
    w = check_mtime_after_receipt(src, now)          # 3. one-way alarm
    if w:
        warnings.append(w)

    tr = timestamper.timestamp_path(str(src), expected_sha256=receipt)  # 4. soft degrade

    token_path = token_sha256 = None                 # 5. token sidecar (hard fail)
    if tr.token_der is not None:
        serial = tr.serial if tr.serial is not None else "noserial"
        token_path = "%s.%s.tsr" % (receipt, serial)
        (out / token_path).write_bytes(tr.token_der)
        token_sha256 = hashlib.sha256(tr.token_der).hexdigest()

    manifest = EvidenceManifest(                      # uses the RECEIPT-TIME stat snapshot
        schema_name=SCHEMA_NAME, schema_version=SCHEMA_VERSION,
        tool={"name": TOOL_NAME, "version": tool_version},
        artifact={"filename": src.name, "sha256": receipt, "size_bytes": stat.st_size,
                  "source_mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                  "received_at": (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).isoformat()},
        timestamp=_timestamp_block(tr, token_path, token_sha256),
        custody_log_path=custody_name, warnings=warnings)

    tmp_manifest = out / (receipt + ".manifest.json.tmp")  # 6. manifest = atomic commit point
    tmp_manifest.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=True),
                            encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)          # atomic promote (hard fail) == THE commit
    try:
        append_custody_event(out / custody_name, "archived", now=now,  # 7. custody ONLY AFTER commit
                             artifact_sha256=receipt, actor=actor)      #    (never claim a non-commit)
    except Exception as exc:                          # ANY post-commit append failure (unwritable
        # OSerror OR a malformed preexisting custody log -> json/Key error): the manifest IS
        # committed, so surface the stranded state EXPLICITLY (recoverable via on_exists=
        # 'append_event'). Excludes BaseException (KeyboardInterrupt/SystemExit) by design.
        raise CustodyAppendError(manifest_path, exc) from exc
    return manifest


def _load_manifest(manifest_path) -> EvidenceManifest:
    d = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return EvidenceManifest(
        schema_name=d["schema_name"], schema_version=d["schema_version"],
        tool=d["tool"], artifact=d["artifact"], timestamp=d["timestamp"],
        custody_log_path=d["custody_log_path"], warnings=d["warnings"])


# --------------------------------------------------------------------------- #
# TSA EDGE -- the real RFC 3161 timestamper (lazy: imports rfc3161-client +
# requests INSIDE its methods so importing this module stays network/ML-free).
# The NETWORK + library decode lives in a thin, monkeypatchable _tsa_roundtrip;
# the TimestampResult assembly (imprint guard, verify-on-store, fail-closed,
# degrade) is pure logic the offline tests drive by monkeypatching it.
# --------------------------------------------------------------------------- #
@dataclass
class _RoundtripResult:
    http_status: Optional[int]
    token_der: Optional[bytes]
    gen_time_iso: Optional[str]
    serial: Optional[int]
    imprint_hex: Optional[str]
    decoded: Any  # the rfc3161_client TimeStampResponse (for verify); None in fakes


class _TsaError(Exception):
    """A TSA round-trip failure carrying a SPECIFIC degrade reason (design 8
    vocabulary), so timestamp_path never collapses bad_pki_status / decode into a
    generic http_error."""
    def __init__(self, reason: str, *, http_status: Optional[int] = None):
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def _classify_network_error(exc: Exception) -> str:
    """Map a requests/transport exception to the design 8 unavailable-reason vocab."""
    name = type(exc).__name__
    if "Timeout" in name:
        return "timeout"
    if "ConnectionError" in name or "ConnectTimeout" in name or "NameResolution" in name:
        return "offline"
    return "http_error:%s" % name


# _full_verification lives in the PURE CORE (above); the edge reuses it.


def _tsa_roundtrip(tsa_url: str, req_der: bytes, timeout: int) -> "_RoundtripResult":
    """The ONLY network + rfc3161-client-decode touch-point (lazy imports). Raises a
    _TsaError with a SPECIFIC reason on bad status / decode / non-GRANTED PKIStatus;
    lets requests' own ConnectionError/Timeout propagate (mapped by the caller)."""
    import requests  # lazy: imported here, never at module top (import-purity)
    from rfc3161_client import decode_timestamp_response
    resp = requests.post(tsa_url, data=req_der,
                         headers={"Content-Type": "application/timestamp-query"},
                         timeout=timeout)
    if resp.status_code != 200:
        raise _TsaError("http_error:%d" % resp.status_code, http_status=resp.status_code)
    try:
        tsr = decode_timestamp_response(resp.content)
    except Exception:
        raise _TsaError("decode_error", http_status=200)
    if int(tsr.status) != 0:                 # PKIStatus GRANTED == 0
        raise _TsaError("bad_pki_status:%s" % tsr.status, http_status=200)
    info = tsr.tst_info
    return _RoundtripResult(
        http_status=200, token_der=tsr.time_stamp_token(),
        gen_time_iso=info.gen_time.astimezone(timezone.utc).isoformat(),
        serial=int(info.serial_number), imprint_hex=info.message_imprint.message.hex(),
        decoded=tsr)


class Rfc3161Timestamper:
    """RFC 3161 Option-A timestamper. tsa_url configurable (freeTSA default). Verifies
    against root_cert_pem, or the bundled freeTSA root when tsa_url is freeTSA. A
    non-freeTSA URL with no supplied root FAILS CLOSED to unverified/no_root_configured.
    """
    def __init__(self, tsa_url: str = FREETSA_URL, *, root_cert_pem: Optional[bytes] = None,
                 timeout: int = 30):
        self.tsa_url = tsa_url
        self._root_cert_pem = root_cert_pem
        self.timeout = timeout

    def _resolve_root(self) -> Optional[bytes]:
        if self._root_cert_pem is not None:
            return self._root_cert_pem
        if self.tsa_url == FREETSA_URL:
            return load_default_root_cert_pem()
        return None  # fail closed

    def timestamp_path(self, path: str, *, expected_sha256: str) -> TimestampResult:
        scheme = "https" if self.tsa_url.lower().startswith("https") else "http"
        no_transport = {"scheme": scheme, "http_status": None}
        try:
            from rfc3161_client import HashAlgorithm, TimestampRequestBuilder
        except Exception as exc:
            return TimestampResult(status="unavailable",
                                   reason="dependency_unavailable:%s" % type(exc).__name__,
                                   tsa_url=self.tsa_url, transport=no_transport)
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            return TimestampResult(status="unavailable", reason="read_error:%s" % type(exc).__name__,
                                   tsa_url=self.tsa_url, transport=no_transport)
        if not data:                              # empty artifact cannot be timestamped
            return TimestampResult(status="unavailable", reason="empty_file",
                                   tsa_url=self.tsa_url, transport=no_transport)
        req = (TimestampRequestBuilder().data(data).hash_algorithm(HashAlgorithm.SHA256)
               .nonce(nonce=True).cert_request(cert_request=True).build())
        try:
            rr = _tsa_roundtrip(self.tsa_url, req.as_bytes(), self.timeout)
        except _TsaError as exc:
            return TimestampResult(status="unavailable", reason=exc.reason, tsa_url=self.tsa_url,
                                   transport={"scheme": scheme, "http_status": exc.http_status})
        except Exception as exc:                  # transport-level (ConnectionError/Timeout/...)
            return TimestampResult(status="unavailable", reason=_classify_network_error(exc),
                                   tsa_url=self.tsa_url, transport=no_transport)
        transport = {"scheme": scheme, "http_status": rr.http_status}
        # TOCTOU guard: the echoed imprint MUST equal the receipt hash.
        if rr.imprint_hex != expected_sha256:
            return TimestampResult(status="unverified", reason="imprint_mismatch",
                                   tsa_url=self.tsa_url, transport=transport, gen_time=rr.gen_time_iso,
                                   serial=rr.serial, token_der=rr.token_der,
                                   verification=_full_verification(granted=True))
        root_pem = self._resolve_root()
        if root_pem is None:                      # fail closed (non-freeTSA, no root)
            return TimestampResult(status="unverified", reason="no_root_configured",
                                   tsa_url=self.tsa_url, transport=transport, gen_time=rr.gen_time_iso,
                                   serial=rr.serial, token_der=rr.token_der,
                                   verification=_full_verification(granted=True, imprint_match=True))
        try:                                      # verify-on-store (needs decoded + request)
            from cryptography import x509
            from rfc3161_client import VerifierBuilder
            roots = x509.load_pem_x509_certificates(root_pem)
            vb = VerifierBuilder.from_request(req)
            for c in roots:
                vb = vb.add_root_certificate(c)
            vb.build().verify(rr.decoded, hashed_message=bytes.fromhex(expected_sha256))
        except Exception as exc:                  # VerificationError or any verify-path failure
            return TimestampResult(status="unverified", reason="verify_failed:%s" % type(exc).__name__,
                                   tsa_url=self.tsa_url, transport=transport, gen_time=rr.gen_time_iso,
                                   serial=rr.serial, token_der=rr.token_der,
                                   verification=_full_verification(granted=True, imprint_match=True))
        return TimestampResult(status="verified", reason=None, tsa_url=self.tsa_url,
                               transport=transport, gen_time=rr.gen_time_iso, serial=rr.serial,
                               token_der=rr.token_der,
                               verification=_full_verification(granted=True, chain_ok=True,
                                   nonce_ok=True, eku_timestamping=True, imprint_match=True,
                                   verified=True))
