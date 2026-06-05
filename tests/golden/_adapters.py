"""Flock-format pipeline-configuration adapters for the Simpsonville golden tests.

These are NOT part of magpie's generic engine. They are the jurisdiction-specific
configuration a real Flock-audit run supplies (the same mapping the pilot's clean()
did), kept here so derive_geo stays generic (design doc, Decision 2). Pure stdlib.
"""
from __future__ import annotations

import re

_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL",
    "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT",
    "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})


def extract_state(org):
    """Last US-state two-letter token in a free-form Org Name, else None.

    'Houston TX Police Department' -> 'TX'. A spelled-out 'South Carolina ...' with no
    two-letter token is special-cased to 'SC' (the pilot's home state). Mirrors the
    pilot build_cache.clean() exactly.
    """
    if not isinstance(org, str):
        return None
    toks = [t.upper() for t in re.findall(r"[A-Za-z]{2,}", org) if t.upper() in _US_STATES]
    if toks:
        return toks[-1]
    if re.search(r"south carolina", org, re.IGNORECASE):
        return "SC"
    return None


def reason_category(reason):
    """The Flock standardized category: the text before ' - ' (stripped); '' if blank."""
    if not isinstance(reason, str):
        return ""
    return reason.split(" - ")[0].strip()
