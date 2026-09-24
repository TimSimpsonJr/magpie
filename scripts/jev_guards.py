"""ASCII only. PURE: local send guards for Magpie's optional Jev features (spec 1.1).

Before any text leaves the machine, ``guard()`` screens it for PII (the same structured
``DEFAULT_PII_PATTERNS`` pii-sweep uses; no spaCy NER, so person names can be sent) and for
secrets (credential-shaped patterns plus a high-entropy token scan). A hit GATES the text
(Part A routes the claim to ``verify``, Part B reports ``skipped``); nothing is redacted.

Fail-safe import: ``scripts.pii_sweep`` imports pandas. If that import fails for any reason,
every text counts as a PII hit, so a broken environment can never send unscreened text.
The import is lazy, so importing this module stays stdlib-only.
"""
from __future__ import annotations

import collections
import functools
import importlib
import math
import re

# Ordered: the first matching pattern names the kind.
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "pem": re.compile(r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----"),
    "api_key": re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"),
    "aws_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{22,}"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    # The header form allows an auth-scheme word before the credential, so a short
    # "Authorization: Bearer abc.def.ghi" is still caught (the scheme word alone is < 8 chars).
    "bearer": re.compile(
        r"(?i)\bauthorization\s*[:=]\s*(?:(?:bearer|basic|token)\s+)?\S{8,}"
        r"|\bbearer\s+[A-Za-z0-9._~+/=\-]{16,}"),
    "secret_kv": re.compile(
        r"(?i)\b[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key"
        r"|private[_\-]?key|credential)s?[\w.\-]*[\"']?\s*[:=]\s*[\"']?[^\s\"',;]+"),
}
HIGH_ENTROPY_RE = re.compile(r"[A-Za-z0-9+/=_\-]{32,}")
_HEX_ONLY_RE = re.compile(r"[0-9a-fA-F]+")
ENTROPY_MIN_BITS = 4.0        # mixed-alphabet token
HEX_ENTROPY_MIN_BITS = 3.5    # hex-only token (hex tops out at 4.0 bits)
HIGH_ENTROPY = "high_entropy"
PII_UNAVAILABLE = "pii_module_unavailable"
GUARD_PII = "pii"
GUARD_SECRET = "secret"


def shannon_entropy(s: str) -> float:
    """Shannon entropy of ``s`` in bits per character (0.0 for the empty string)."""
    if not s:
        return 0.0
    n = len(s)
    h = -sum((c / n) * math.log2(c / n) for c in collections.Counter(s).values())
    return h + 0.0  # normalize -0.0 to 0.0


def _high_entropy(text: str) -> bool:
    for m in HIGH_ENTROPY_RE.finditer(text):
        tok = m.group(0)
        floor = HEX_ENTROPY_MIN_BITS if _HEX_ONLY_RE.fullmatch(tok) else ENTROPY_MIN_BITS
        if shannon_entropy(tok) >= floor:
            return True
    return False


def secret_hit(text: str) -> str | None:
    """The first matching secret kind, else ``"high_entropy"``, else None."""
    if not text:
        return None
    for kind, rx in SECRET_PATTERNS.items():
        if rx.search(text):
            return kind
    if _high_entropy(text):
        return HIGH_ENTROPY
    return None


@functools.lru_cache(maxsize=1)
def _pii_patterns() -> dict[str, re.Pattern] | None:
    """pii_sweep's DEFAULT_PII_PATTERNS, or None when the module cannot be loaded."""
    try:
        patterns = importlib.import_module("scripts.pii_sweep").DEFAULT_PII_PATTERNS
        return dict(patterns)
    except Exception:  # noqa: BLE001 - any failure must fail safe (treated as PII)
        return None


def pii_hit(text: str) -> str | None:
    """The first matching PII category name, or ``"pii_module_unavailable"`` for every input
    when the PII patterns cannot be loaded."""
    patterns = _pii_patterns()
    if patterns is None:
        return PII_UNAVAILABLE
    if not text:
        return None
    for name, rx in patterns.items():
        if rx.search(text):
            return name
    return None


def guard(*texts: str | None) -> str | None:
    """``"pii"`` if any text has a PII hit, else ``"secret"`` if any has a secret hit, else None.

    None entries are skipped. When the PII patterns are unavailable the result is always
    ``"pii"``, whatever the inputs.
    """
    if _pii_patterns() is None:
        return GUARD_PII
    present = [t for t in texts if t is not None]
    if any(pii_hit(t) for t in present):
        return GUARD_PII
    if any(secret_hit(t) for t in present):
        return GUARD_SECRET
    return None
