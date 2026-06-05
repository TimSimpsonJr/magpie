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
