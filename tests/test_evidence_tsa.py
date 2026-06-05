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
