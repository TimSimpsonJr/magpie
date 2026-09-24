"""ASCII only. Offline tests for scripts/jev_guards.py (Task 2): the local secret + PII send
guards. The guards gate (never redact); a broken pii_sweep import must fail safe to "pii"."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import jev_guards as jg
from scripts.jev_guards import guard, pii_hit, secret_hit

# Fixed, synthetic high-entropy strings (no real credentials anywhere in this file).
RANDOM_B64_40 = "Zq8Lr3Tn0Vx7Kp2Wm5Yb9Hc4Jd6Fg1Ss8Ae3Qu0"
RANDOM_HEX_40 = "3f9a0c7e41b2d86f5a0e9c3b7d1f428e6a5c0b9d"


@pytest.fixture
def fresh_pii_cache():
    jg._pii_patterns.cache_clear()
    yield
    jg._pii_patterns.cache_clear()


# ---------------------------------------------------------------- secret_hit

def test_pem_header_block():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----"
    assert secret_hit(text) == "pem"


def test_pem_certificate_header():
    assert secret_hit("-----BEGIN CERTIFICATE-----") == "pem"


def test_api_key_prefix():
    assert secret_hit("key sk-or-v1-abcdefghijklmnop1234") == "api_key"


def test_aws_key():
    assert secret_hit("AKIAABCDEFGHIJKLMNOP") == "aws_key"


def test_github_token():
    assert secret_hit("ghp_" + "a1" * 18) == "github_token"


def test_github_pat():
    assert secret_hit("github_pat_" + "A1b2" * 6) == "github_token"


def test_slack_token():
    assert secret_hit("xoxb-1234567890-abcdef") == "slack_token"


def test_authorization_bearer_header():
    assert secret_hit("Authorization: Bearer abc.def.ghi123") == "bearer"


def test_bare_bearer_token():
    assert secret_hit("bearer abcdefghij0123456789") == "bearer"


@pytest.mark.parametrize("text", [
    "password=hunter2",
    "secret: s3cr3t",
    "api_key = 'x9'",
    '"token": "abcd"',
    "DB_PASSWORD=letmein",
    "aws_access_key: foo",
])
def test_secret_kv(text):
    assert secret_hit(text) == "secret_kv"


def test_random_base64_is_high_entropy():
    assert jg.shannon_entropy(RANDOM_B64_40) >= jg.ENTROPY_MIN_BITS
    assert secret_hit(f"value {RANDOM_B64_40} end") == "high_entropy"


def test_random_hex_is_high_entropy():
    assert jg.shannon_entropy(RANDOM_HEX_40) >= jg.HEX_ENTROPY_MIN_BITS
    assert secret_hit(f"digest {RANDOM_HEX_40}") == "high_entropy"


def test_low_entropy_long_run_is_clean():
    assert secret_hit("a" * 40) is None
    assert secret_hit("0" * 64) is None


def test_long_readable_identifier_is_clean():
    token = "internationalization_localization"
    assert len(token) >= 32
    assert secret_hit(token) is None


@pytest.mark.parametrize("text", [
    "The officer ran 482 searches in March 2026.",
    "The secretary signed the memo.",
    "password reset policy",
    "bearer bonds",
    "basic training",
    "The token economy was discussed at the meeting.",
    "",
])
def test_ordinary_prose_is_clean(text):
    assert secret_hit(text) is None


def test_first_pattern_names_the_kind():
    # pem precedes secret_kv in SECRET_PATTERNS order.
    assert secret_hit("-----BEGIN PRIVATE KEY----- password=x") == "pem"


def test_secret_patterns_order():
    assert list(jg.SECRET_PATTERNS) == [
        "pem", "api_key", "aws_key", "github_token", "slack_token", "bearer", "secret_kv"]


# ------------------------------------------------------------------- pii_hit

@pytest.mark.parametrize("text,kind", [
    ("Call 864-555-0100", "phone"),
    ("SSN 123-45-6789", "ssn"),
    ("a@b.org", "email"),
    ("DOB", "dob_kw"),
    ("03/15/1980", "possible_birthdate"),
    ("8645550100", "phone_compact"),
])
def test_pii_categories(text, kind):
    assert pii_hit(text) == kind


def test_pii_clean_name_and_number():
    assert pii_hit("Officer Ramirez ran 482 searches") is None


def test_pii_empty_is_clean():
    assert pii_hit("") is None


# --------------------------------------------------------------------- guard

def test_guard_clean():
    assert guard("clean", "also clean") is None


def test_guard_pii_in_any_text():
    assert guard("clean", "864-555-0100") == "pii"


def test_guard_secret():
    assert guard("password=x") == "secret"


def test_guard_pii_precedes_secret():
    assert guard("password=x 864-555-0100") == "pii"


def test_guard_pii_precedes_secret_across_texts():
    assert guard("password=x", "864-555-0100") == "pii"


def test_guard_skips_none():
    assert guard(None, "clean") is None
    assert guard(None, None) is None


def test_guard_no_args():
    assert guard() is None


# ---------------------------------------------------- fail-safe PII import

def _assert_fail_safe():
    assert jg._pii_patterns() is None
    assert pii_hit("hello") == "pii_module_unavailable"
    assert pii_hit("") == "pii_module_unavailable"
    assert guard("hello") == "pii"
    assert guard("") == "pii"
    assert guard(None) == "pii"
    assert guard() == "pii"


def test_pii_module_blocked_in_sys_modules(monkeypatch, fresh_pii_cache):
    monkeypatch.setitem(sys.modules, "scripts.pii_sweep", None)
    jg._pii_patterns.cache_clear()
    _assert_fail_safe()


def test_import_module_raises(monkeypatch, fresh_pii_cache):
    def boom(name, *args, **kwargs):
        raise RuntimeError("broken env")

    monkeypatch.setattr(jg.importlib, "import_module", boom)
    jg._pii_patterns.cache_clear()
    _assert_fail_safe()


def test_patterns_missing_attribute(monkeypatch, fresh_pii_cache):
    class Stub:
        pass

    monkeypatch.setitem(sys.modules, "scripts.pii_sweep", Stub())
    jg._pii_patterns.cache_clear()
    _assert_fail_safe()


def test_pii_patterns_restored_after_cache_clear(fresh_pii_cache):
    pats = jg._pii_patterns()
    assert pats is not None
    assert "phone" in pats


# ------------------------------------------------------------ light import

def test_import_does_not_load_pandas():
    code = ("import sys; import scripts.jev_guards; "
            "assert 'pandas' not in sys.modules, 'pandas'; "
            "assert 'scripts.pii_sweep' not in sys.modules, 'pii_sweep'")
    p = subprocess.run([sys.executable, "-c", code],
                       cwd=str(Path(__file__).resolve().parent.parent),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
