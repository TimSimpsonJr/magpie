# Phase 9 archive-evidence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** Build `scripts/evidence.py` (evidence provenance + chain-of-custody) and
the `archive-evidence` skill: SHA-256-on-receipt, an RFC 3161 trusted timestamp from
a free TSA, an append-only hash-chained custody log, and a provenance manifest.

**Architecture:** Pure core (stdlib + hashlib; deterministic, clock INJECTED) plus a
lazy RFC 3161 TSA edge behind an INJECTABLE `Timestamper` protocol (golden suite uses
a `FakeTimestamper`; one live test behind a `tsa` marker), mirroring the suite's
pure-core / engine-at-the-edge split (pii_sweep, ingest, citation).

**Tech Stack:** Python 3.12, hashlib/json/dataclasses (stdlib), `rfc3161-client`
1.0.6 (Apache-2.0; only dep is `cryptography`, already pinned), `requests` (present),
the bundled freeTSA root cert.

**Source of truth:** `docs/plans/2026-06-05-magpie-phase9-archive-evidence-design.md`.
Read it before implementing. Research facts:
`skills/archive-evidence/references/prior-art.md`.

---

## Conventions for the implementer (READ FIRST)

- You are ALREADY on the feature branch `feat/phase9-archive-evidence`. Commit
  directly to it. Do NOT create or switch branches.
- Read ONLY this plan, the design doc above, and files you create. Do NOT open other
  repo files (several carry non-ASCII bytes that block the Read tool). All house
  patterns you need are INLINE below.
- Keep every file you write ASCII-only (no em-dashes, smart quotes, or non-ASCII).
- Run tests with the venv python directly (the shell is -NoProfile; never bare
  `python`):
  `& .venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`
  Offline-only run of the whole suite:
  `& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
  (use `-m` MARKER exclusion, NOT `-k` name matching: `-k "not tsa"` also drops the
  whole `test_evidence_tsa.py` file because the FILENAME contains "tsa")
- House style (from citation.py / ingest_gate.py): module docstring states PURE vs
  EDGE and that the clock is injected; `from __future__ import annotations`; named
  module CONSTANTS with a rationale comment; dataclasses for records; thorough
  docstrings naming the rigor invariant; "flag-don't-fake / never a fake 0" honesty.

---

## Task 0: Dependencies + pytest marker

**Files:**
- Modify: `requirements-dev.txt` (append the pin)
- Modify: `pyproject.toml` (add the `tsa` marker)

**Step 1:** Append to `requirements-dev.txt` (after the x-ray line):
```
# Phase 9 archive-evidence: RFC 3161 trusted timestamping (Apache-2.0). Sole dep is
# cryptography (already pinned). Prebuilt cp39-abi3 win_amd64 wheel; does no network.
rfc3161-client==1.0.6
```

**Step 2:** In `pyproject.toml`, find the `[tool.pytest.ini_options]` `markers` list
(it has spacy/docling/xray) and add:
```
    "tsa: tests that hit a live RFC 3161 Time-Stamp Authority over the network (select with -m tsa)",
```

**Step 3:** Confirm the dep is importable (it was installed at the research gate):
Run: `& .venv\Scripts\python.exe -c "import rfc3161_client; print(rfc3161_client.__name__)"`
Expected: `rfc3161_client`

**Step 4: Commit**
```
git add requirements-dev.txt pyproject.toml
git commit -m "build(archive-evidence): pin rfc3161-client + add tsa pytest marker"
```

---

## Task 1: evidence.py pure core (hash, mtime, custody, manifest, protocol)

**Files:**
- Create: `scripts/evidence.py`
- Test: `tests/test_evidence.py`

This task builds the entire PURE core + the injectable seam. No network. Write the
tests first, watch them fail, then implement.

**Step 1: Write the failing tests** (`tests/test_evidence.py`):
```python
"""TDD for scripts/evidence.py pure core (no network; FakeTimestamper)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import evidence
from scripts.evidence import (
    EvidenceManifest,
    FakeTimestamper,
    TimestampResult,
    append_custody_event,
    archive_evidence,
    check_mtime_after_receipt,
    sha256_file,
    verify_custody_chain,
)

UTC = timezone.utc
T0 = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UTC)


def _write(tmp_path, name, data=b"hello evidence"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# --- sha256_file ---
def test_sha256_file_matches_hashlib(tmp_path):
    data = b"a" * (1024 * 1024 + 7)  # spans the 1 MiB chunk boundary
    p = _write(tmp_path, "f.bin", data)
    assert sha256_file(p) == hashlib.sha256(data).hexdigest()


# --- mtime one-way alarm ---
def test_mtime_alarm_fires_when_file_newer_than_receipt(tmp_path):
    p = _write(tmp_path, "f.txt")
    # received_at well BEFORE the file's real mtime (now) -> alarm fires
    w = check_mtime_after_receipt(p, T0)
    assert w is not None and "post-receipt" in w


def test_mtime_alarm_silent_when_receipt_after_mtime(tmp_path):
    p = _write(tmp_path, "f.txt")
    future = datetime.now(UTC) + timedelta(days=1)
    assert check_mtime_after_receipt(p, future) is None


# --- custody chain ---
def test_custody_chain_links_and_genesis(tmp_path):
    log = tmp_path / "c.jsonl"
    e0 = append_custody_event(log, "received", now=T0, artifact_sha256="aa")
    e1 = append_custody_event(log, "archived", now=T0, artifact_sha256="aa")
    assert e0["seq"] == 0 and e0["prev_entry_sha256"] == "0" * 64
    assert e1["seq"] == 1 and e1["prev_entry_sha256"] == e0["entry_sha256"]
    assert verify_custody_chain(log) is True


def test_custody_chain_detects_tamper(tmp_path):
    log = tmp_path / "c.jsonl"
    append_custody_event(log, "received", now=T0, artifact_sha256="aa")
    append_custody_event(log, "archived", now=T0, artifact_sha256="aa")
    lines = log.read_text(encoding="utf-8").splitlines()
    d = json.loads(lines[0]); d["event"] = "TAMPERED"
    lines[0] = json.dumps(d, sort_keys=True, separators=(",", ":"))
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify_custody_chain(log) is False


# --- archive_evidence happy path (FakeTimestamper verified) ---
def _verified_fake():
    return FakeTimestamper(TimestampResult(
        status="verified", reason=None, tsa_url="https://freetsa.org/tsr",
        transport={"scheme": "https", "http_status": 200},
        gen_time="2026-06-05T18:00:00+00:00", serial=42, token_der=b"FAKE-TOKEN",
        verification={"granted": True, "chain_ok": True, "nonce_ok": True,
                      "eku_timestamping": True, "imprint_match": True,
                      "revocation_checked": False, "verified": True}))


def test_archive_evidence_manifest_schema(tmp_path):
    src = _write(tmp_path, "audit.csv", b"col\n1\n")
    out = tmp_path / "out"
    m = archive_evidence(src, timestamper=_verified_fake(), out_dir=out, now=T0)
    d = m.to_dict()
    # required top-level keys
    assert set(d) == {"schema_name", "schema_version", "tool", "artifact",
                      "timestamp", "custody_log_path", "warnings"}
    assert d["schema_name"] == "magpie-archive-evidence"
    assert d["artifact"]["filename"] == "audit.csv"  # basename only
    assert d["artifact"]["sha256"] == sha256_file(src)
    ts = d["timestamp"]
    assert ts["status"] == "verified"
    assert set(ts) >= {"status", "reason", "tsa_url", "transport", "gen_time",
                       "serial", "token_path", "token_sha256", "verification"}
    assert ts["token_sha256"] == hashlib.sha256(b"FAKE-TOKEN").hexdigest()
    # the FULL inner verification object is pinned (no partial dicts)
    assert set(ts["verification"]) == {"granted", "chain_ok", "nonce_ok",
        "eku_timestamping", "imprint_match", "revocation_checked", "verified"}
    assert ts["verification"]["verified"] is True
    assert ts["verification"]["revocation_checked"] is False
    # manifest + token + custody files exist on disk
    assert (out / (d["artifact"]["sha256"] + ".manifest.json")).exists()
    assert (out / ts["token_path"]).exists()
    assert (out / d["custody_log_path"]).exists()
    # NO absolute paths anywhere in the manifest JSON
    blob = json.dumps(d)
    assert str(tmp_path) not in blob and ":\\" not in blob


def test_archive_evidence_reason_present_when_not_verified(tmp_path):
    src = _write(tmp_path, "f.txt")
    fake = FakeTimestamper(TimestampResult(
        status="unavailable", reason="offline", tsa_url="https://freetsa.org/tsr",
        transport={"scheme": "https", "http_status": None}))
    m = archive_evidence(src, timestamper=fake, out_dir=tmp_path / "o", now=T0)
    ts = m.to_dict()["timestamp"]
    assert ts["status"] == "unavailable" and ts["reason"] == "offline"
    assert ts["token_path"] is None and ts["token_sha256"] is None
    # even an unavailable timestamp carries the FULL 7-key verification (all False),
    # never a null verification object
    assert set(ts["verification"]) == {"granted", "chain_ok", "nonce_ok",
        "eku_timestamping", "imprint_match", "revocation_checked", "verified"}
    assert ts["verification"]["verified"] is False


def test_archive_evidence_imprint_mismatch_surfaced(tmp_path):
    # The timestamper signals a TOCTOU/imprint mismatch -> it lands in the manifest
    src = _write(tmp_path, "f.txt")
    fake = FakeTimestamper(TimestampResult(
        status="unverified", reason="imprint_mismatch",
        tsa_url="https://freetsa.org/tsr", token_der=b"T", serial=7,
        verification={"imprint_match": False, "verified": False}))
    m = archive_evidence(src, timestamper=fake, out_dir=tmp_path / "o", now=T0)
    ts = m.to_dict()["timestamp"]
    assert ts["status"] == "unverified" and ts["reason"] == "imprint_mismatch"


def test_archive_evidence_idempotent_refuses_overwrite(tmp_path):
    src = _write(tmp_path, "f.txt")
    out = tmp_path / "o"
    archive_evidence(src, timestamper=_verified_fake(), out_dir=out, now=T0)
    with pytest.raises(evidence.ArchiveExistsError):
        archive_evidence(src, timestamper=_verified_fake(), out_dir=out, now=T0)


def test_archive_evidence_append_event_adds_custody(tmp_path):
    src = _write(tmp_path, "f.txt")
    out = tmp_path / "o"
    m = archive_evidence(src, timestamper=_verified_fake(), out_dir=out, now=T0)
    log = out / m.to_dict()["custody_log_path"]
    n0 = len(log.read_text(encoding="utf-8").splitlines())
    archive_evidence(src, timestamper=_verified_fake(), out_dir=out, now=T0,
                     on_exists="append_event")
    n1 = len(log.read_text(encoding="utf-8").splitlines())
    assert n1 == n0 + 1


def test_archive_evidence_local_write_failure_hard_fails(tmp_path):
    # out_dir whose parent is a FILE -> mkdir raises -> hard fail (not fake success)
    blocker = _write(tmp_path, "blocker")
    bad_out = blocker / "sub"
    with pytest.raises(OSError):
        archive_evidence(_write(tmp_path, "f.txt"), timestamper=_verified_fake(),
                         out_dir=bad_out, now=T0)


def test_archive_evidence_uses_receipt_time_stat_snapshot(tmp_path):
    # The timestamper grows the file AFTER receipt; the manifest must record the
    # RECEIPT-time size, not the grown size (the artifact-metadata TOCTOU guard).
    src = _write(tmp_path, "f.txt", b"orig-bytes")
    orig_size = src.stat().st_size

    class GrowingFake:
        def timestamp_path(self, path, *, expected_sha256):
            with open(path, "ab") as fh:
                fh.write(b"APPENDED-AFTER-RECEIPT")
            return TimestampResult(status="unavailable", reason="offline")

    m = archive_evidence(src, timestamper=GrowingFake(), out_dir=tmp_path / "o", now=T0)
    assert m.to_dict()["artifact"]["size_bytes"] == orig_size


def test_archive_evidence_empty_file_still_archived(tmp_path):
    src = _write(tmp_path, "empty.txt", b"")
    fake = FakeTimestamper(TimestampResult(status="unavailable", reason="empty_file",
                                           tsa_url="https://freetsa.org/tsr"))
    m = archive_evidence(src, timestamper=fake, out_dir=tmp_path / "o", now=T0)
    d = m.to_dict()
    assert d["artifact"]["sha256"] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")  # sha256("")
    assert d["timestamp"]["status"] == "unavailable"
    assert d["timestamp"]["reason"] == "empty_file"


def test_fake_timestamper_records_expected_sha256(tmp_path):
    src = _write(tmp_path, "f.txt")
    fake = _verified_fake()
    archive_evidence(src, timestamper=fake, out_dir=tmp_path / "o", now=T0)
    assert fake.calls and fake.calls[0][1] == sha256_file(src)  # (path, expected_sha256)
```

**Step 2: Run to verify they fail**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`
Expected: collection/import error (`scripts.evidence` does not exist).

**Step 3: Implement `scripts/evidence.py` (pure core)**
```python
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
    for i, ln in enumerate(x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()):
        d = json.loads(ln)
        claimed = d.pop("entry_sha256", None)
        if hashlib.sha256(_canonical(d).encode("utf-8")).hexdigest() != claimed:
            return False
        if d.get("seq") != i or d.get("prev_entry_sha256") != prev:
            return False
        prev = claimed
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
                     tool_version: str = "0.1.0", actor: Optional[str] = None,
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

    append_custody_event(out / custody_name, "archived", now=now,  # 6. custody (hard fail)
                         artifact_sha256=receipt, actor=actor)
    tmp_manifest = out / (receipt + ".manifest.json.tmp")  # 7. manifest LAST = atomic commit
    tmp_manifest.write_text(json.dumps(manifest.to_dict(), indent=2, ensure_ascii=True),
                            encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)          # atomic promote (hard fail)
    return manifest


def _load_manifest(manifest_path) -> EvidenceManifest:
    d = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    return EvidenceManifest(
        schema_name=d["schema_name"], schema_version=d["schema_version"],
        tool=d["tool"], artifact=d["artifact"], timestamp=d["timestamp"],
        custody_log_path=d["custody_log_path"], warnings=d["warnings"])
```

**Step 4: Run the tests**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_evidence.py -q`
Expected: all Task-1 tests PASS.

**Step 5: Commit**
```
git add scripts/evidence.py tests/test_evidence.py
git commit -m "feat(archive-evidence): evidence.py pure core + hash-chained custody + manifest"
```

---

## Task 2: Rfc3161Timestamper (the real RFC 3161 TSA edge)

**Files:**
- Modify: `scripts/evidence.py` (append the timestamper class)
- Test: `tests/test_evidence_tsa.py`

The edge lazily imports rfc3161-client + requests. Verify-on-store uses the bundled
freeTSA root; fail-closed for a non-freeTSA URL with no supplied root; the TOCTOU
imprint assertion is mandatory.

**Step 1: Write the failing tests** (`tests/test_evidence_tsa.py`):
```python
"""TDD for the RFC 3161 edge. Offline unit tests stub requests; one live test is
behind the `tsa` marker."""
from __future__ import annotations

import hashlib
from datetime import timezone

import pytest

from scripts import evidence
from scripts.evidence import Rfc3161Timestamper, sha256_file


def test_default_root_cert_fingerprint_pinned():
    # The bundled freeTSA Root CA must be exactly this cert (catch a silent swap).
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    pem = evidence.load_default_root_cert_pem()
    cert = x509.load_pem_x509_certificate(pem)
    assert cert.fingerprint(hashes.SHA256()).hex() == (
        "a6379e7cecc05faa3cbf076013d745e327bbbaa38c0b9af22469d4701d18aabc")


def test_fail_closed_non_freetsa_without_root(monkeypatch, tmp_path):
    # A non-freeTSA URL with no supplied root must NOT verify; it returns a token
    # (network ok) but status unverified / no_root_configured -- never opportunistic
    # trust. We stub the network so the test is offline.
    p = tmp_path / "f.txt"; p.write_bytes(b"data")
    receipt = sha256_file(p)
    ts = Rfc3161Timestamper(tsa_url="https://example.invalid/tsr")
    # Stub: make the POST + decode return a GRANTED token whose imprint == receipt.
    # (See the plan note: monkeypatch evidence._tsa_roundtrip to a fake returning
    # gen_time/serial/token_der/imprint == receipt.)
    monkeypatch.setattr(evidence, "_tsa_roundtrip", _fake_roundtrip(receipt))
    res = ts.timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "unverified" and res.reason == "no_root_configured"
    assert res.token_der is not None  # token kept for later verification
    assert set(res.verification) == {"granted", "chain_ok", "nonce_ok",
        "eku_timestamping", "imprint_match", "revocation_checked", "verified"}
    assert res.verification["imprint_match"] is True
    assert res.verification["verified"] is False


def test_imprint_mismatch_is_toctou_guard(monkeypatch, tmp_path):
    p = tmp_path / "f.txt"; p.write_bytes(b"data")
    receipt = sha256_file(p)
    ts = Rfc3161Timestamper()  # freeTSA default
    # Stub a token whose imprint is for DIFFERENT bytes (simulates file changed)
    monkeypatch.setattr(evidence, "_tsa_roundtrip", _fake_roundtrip("deadbeef" * 8))
    res = ts.timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "unverified" and res.reason == "imprint_mismatch"
    assert res.verification["imprint_match"] is False
    assert res.verification["verified"] is False


def test_offline_http_error_is_unavailable(monkeypatch, tmp_path):
    p = tmp_path / "f.txt"; p.write_bytes(b"data")
    receipt = sha256_file(p)
    ts = Rfc3161Timestamper()

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(evidence, "_tsa_roundtrip", _boom)
    res = ts.timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "unavailable"
    assert res.reason.startswith("http_error")  # RuntimeError -> http_error:RuntimeError


def test_empty_file_is_unavailable_empty_file(tmp_path):
    # An empty file cannot be RFC 3161 timestamped (the client rejects empty input);
    # the edge degrades to unavailable/empty_file BEFORE any network call.
    p = tmp_path / "empty.txt"; p.write_bytes(b"")
    res = Rfc3161Timestamper().timestamp_path(str(p), expected_sha256="00" * 32)
    assert res.status == "unavailable" and res.reason == "empty_file"


def test_bad_pki_status_reason_preserved(monkeypatch, tmp_path):
    # A non-GRANTED PKIStatus must surface as reason 'bad_pki_status:...', NOT collapse
    # to a generic http_error (the design 8 reason vocabulary).
    p = tmp_path / "f.txt"; p.write_bytes(b"data")
    receipt = sha256_file(p)

    def _bad(*a, **k):
        raise evidence._TsaError("bad_pki_status:2", http_status=200)
    monkeypatch.setattr(evidence, "_tsa_roundtrip", _bad)
    res = Rfc3161Timestamper().timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "unavailable" and res.reason == "bad_pki_status:2"
    assert res.transport["http_status"] == 200


def test_verify_failed_reason(monkeypatch, tmp_path):
    # freeTSA default -> bundled root resolves; a fake roundtrip with decoded=None
    # makes verify() raise -> reason 'verify_failed' (not a collapsed bucket).
    p = tmp_path / "f.txt"; p.write_bytes(b"data")
    receipt = sha256_file(p)
    monkeypatch.setattr(evidence, "_tsa_roundtrip", _fake_roundtrip(receipt))
    res = Rfc3161Timestamper().timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "unverified" and res.reason.startswith("verify_failed")
    assert res.verification["imprint_match"] is True
    assert res.verification["verified"] is False


@pytest.mark.tsa
def test_live_freetsa_roundtrip_verifies(tmp_path):
    p = tmp_path / "f.txt"; p.write_bytes(b"magpie phase 9 live tsa test")
    receipt = sha256_file(p)
    res = Rfc3161Timestamper().timestamp_path(str(p), expected_sha256=receipt)
    assert res.status == "verified"
    assert res.gen_time is not None and res.serial is not None
    assert res.token_der is not None
    assert res.verification["verified"] is True
    assert res.transport["scheme"] == "https" and res.transport["http_status"] == 200


# helper: build a fake _tsa_roundtrip returning a tiny object with the fields the
# timestamper reads. See the plan's implementation note for the exact attributes.
def _fake_roundtrip(imprint_hex):
    def _f(tsa_url, req_der, timeout):
        return evidence._RoundtripResult(
            http_status=200, token_der=b"FAKE", gen_time_iso="2026-06-05T18:00:00+00:00",
            serial=99, imprint_hex=imprint_hex, decoded=None)
    return _f
```

**Step 2: Run to verify they fail**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_evidence_tsa.py -q -m "not tsa"`
Expected: FAIL (Rfc3161Timestamper / _tsa_roundtrip not defined).
(NOTE: use `-m "not tsa"`, NOT `-k "not tsa"` -- the filename contains "tsa" so a
`-k` name filter would deselect every test in this file.)

**Step 3: Implement the edge** (append to `scripts/evidence.py`).

Design the edge so the NETWORK + library calls live in a thin, monkeypatchable
`_tsa_roundtrip(tsa_url, req_der, timeout) -> _RoundtripResult`, and the
TimestampResult assembly (imprint guard, verify-on-store, fail-closed, degrade) is
pure logic the offline tests drive by monkeypatching `_tsa_roundtrip`:
```python
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
```

Note for the offline tests: `_fake_roundtrip` returns a `_RoundtripResult` with
`decoded=None`. The fail-closed and imprint-mismatch tests return BEFORE the verify
block (so decoded=None is never dereferenced). Do not add a verify path that needs
`decoded` before those early returns.

**Step 4: Run the offline tests**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_evidence_tsa.py -q -m "not tsa"`
Expected: PASS (the 4 offline tests).

**Step 5: Run the live test once to confirm the real path (optional, needs network)**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_evidence_tsa.py -q -m tsa`
Expected: PASS (a real freeTSA round-trip verifies).

**Step 6: Commit**
```
git add scripts/evidence.py tests/test_evidence_tsa.py
git commit -m "feat(archive-evidence): RFC 3161 timestamper edge (Option A, verify-on-store, fail-closed, TOCTOU guard)"
```

---

## Task 3: archive-evidence SKILL.md + skill smoke test

**Files:**
- Create: `skills/archive-evidence/SKILL.md`
- Test: `tests/test_archive_evidence_skill.py`

**Step 1: Write the failing smoke test** (`tests/test_archive_evidence_skill.py`):
```python
"""Smoke test for the archive-evidence SKILL.md (mirrors test_investigate_skill)."""
from __future__ import annotations

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent / "skills" / "archive-evidence" / "SKILL.md"


def _frontmatter_and_body(p):
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---")
    _, fm, body = text.split("---", 2)
    return yaml.safe_load(fm), body


def test_frontmatter():
    fm, _ = _frontmatter_and_body(SKILL)
    assert fm["name"] == "archive-evidence"
    assert "version" in fm
    d = fm["description"].lower()
    assert "provenance" in d or "custody" in d
    assert "timestamp" in d or "evidence" in d


def test_body_documents_contracts():
    _, body = _frontmatter_and_body(SKILL)
    low = body.lower()
    assert "evidence.py" in body                       # names the engine
    assert "archive_evidence" in body
    assert "on receipt" in low or "on-receipt" in low  # receipt-first ordering
    assert "rfc 3161" in low or "rfc3161" in low
    assert "custody" in low and "manifest" in low
    assert "librarian" in low                          # the note split
    assert "tamper-evident" in low                     # honest custody limit
    # honest limits / degrade vocabulary
    assert "unavailable" in low and "verified" in low
    assert "does not prove" in low or "does not establish" in low
```

**Step 2: Run to verify it fails**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_archive_evidence_skill.py -q`
Expected: FAIL (SKILL.md missing).

**Step 3: Write `skills/archive-evidence/SKILL.md`** (ASCII-only). Frontmatter +
body following the investigate skill's shape. It MUST:
- frontmatter: `name: archive-evidence`, a third-person trigger-rich `description`
  (mentions provenance/chain-of-custody, timestamp, FOIA receipt), `version: 0.1.0`.
- Lead: archive-evidence records provenance on receipt; call one engine
  (scripts/evidence.py archive_evidence); the skill orchestrates the Librarian note.
- Section: receipt-FIRST ordering (hash before any processing).
- Section: the RFC 3161 timestamp (Option A; freeTSA default; verify-on-store;
  fail-closed; the degrade vocabulary verified/unverified/unavailable with a reason).
- Section: the custody log (append-only, hash-chained, tamper-EVIDENT not
  tamper-PROOF; the token is the external anchor).
- Section: output -- evidence.py writes LOCAL artifacts (manifest.json, .tsr,
  custody.jsonl); the SKILL routes a Librarian provenance NOTE (filename + receipt
  sha256 + timestamp status/gen_time + custody pointer; raw token + local paths stay
  LOCAL).
- Section: honest limits -- a timestamp attests a hash existed at a time; it DOES
  NOT prove authorship, source, or that the file is unaltered relative to any other
  moment; verification does not check revocation; mtime is a one-way alarm; TLS and
  token trust are separate layers.
- Closing: engine module scripts/evidence.py; the new dep rfc3161-client; the
  bundled freeTSA root; no .mcp.json ships.

**Step 4: Run the smoke test**
Run: `& .venv\Scripts\python.exe -m pytest tests/test_archive_evidence_skill.py -q`
Expected: PASS.

**Step 5: Commit**
```
git add skills/archive-evidence/SKILL.md tests/test_archive_evidence_skill.py
git commit -m "feat(archive-evidence): orchestration SKILL.md + smoke test"
```

---

## Task 4: Integration -- full offline suite green

**Files:** none (verification only).

**Step 1:** Run the whole offline suite:
`& .venv\Scripts\python.exe -m pytest -m "not docling and not spacy and not xray and not tsa" -q`
Expected: all prior tests still pass + the new evidence/skill tests pass (no
regressions; the baseline was 431 passed / 1 skipped).

**Step 2:** Run the import-purity check (the module must not pull network/ML at
import): `& .venv\Scripts\python.exe -c "import scripts.evidence; import sys; assert 'requests' not in sys.modules and 'rfc3161_client' not in sys.modules; print('import-pure OK')"`
Expected: `import-pure OK` (the edge imports are lazy).

**Step 3:** If green, this task is done. MANIFEST.md regeneration + the PR happen in
the main thread (NOT a subagent -- MANIFEST has non-ASCII).

---

## Done criteria

- `scripts/evidence.py`: pure core (sha256_file, custody chain, mtime alarm,
  manifest, archive_evidence) + the lazy Rfc3161Timestamper edge; import-pure.
- Tests green: `tests/test_evidence.py`, `tests/test_evidence_tsa.py` (offline +
  the `tsa`-marked live test), `tests/test_archive_evidence_skill.py`.
- `skills/archive-evidence/SKILL.md` + bundled `references/freetsa_cacert.pem` (done)
  + `references/prior-art.md` (done).
- `requirements-dev.txt` pin + `pyproject.toml` `tsa` marker.
- No regressions in the offline suite.

## Honest-limit + safety checklist (do NOT regress these)

- TOCTOU: the timestamper asserts token imprint == expected_sha256; a mismatch is
  unverified/imprint_mismatch with the token kept, NEVER a silent verified.
- Fail-closed: a non-freeTSA URL with no root -> unverified/no_root_configured, never
  opportunistic trust.
- Degrade vs hard-fail: TSA failure soft-degrades (status unavailable/unverified +
  specific reason); any local write failure raises.
- Idempotency: default on_exists='error' refuses overwrite.
- Manifest carries NO absolute paths; reason is present whenever status != verified.
- evidence.py imports no Librarian and is import-pure (no eager requests/rfc3161).
