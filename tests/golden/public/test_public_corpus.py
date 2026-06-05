import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_SLICE = _REPO / "corpus" / "public" / "spokane_flock_slice.csv"
_EXPECTED = Path(__file__).resolve().parent / "expected_public.json"

pytestmark = pytest.mark.skipif(
    not (_SLICE.exists() and _EXPECTED.exists()),
    reason="public slice + frozen goldens not bundled yet (RANGE permission pending)",
)

def _run_pipeline(df):
    # OFFLINE only: load -> derive -> recipe/stats. No spaCy/docling.
    # (Wire via scripts.load_table / scripts.derive / scripts.recipe / scripts.stats,
    #  using the same adapter helpers as tests/golden/_adapters.py. Filled when the
    #  slice lands; the structure is what matters now.)
    raise NotImplementedError

def test_public_slice_reproduces_frozen_goldens():
    import pandas as pd
    df = pd.read_csv(_SLICE, dtype=str, keep_default_na=False, na_values=[""])
    got = _run_pipeline(df)
    expected = json.loads(_EXPECTED.read_text(encoding="utf-8"))
    assert got == expected
