import pandas as pd
import pytest
from tools.build_public_slice import build_slice

def _frame():
    # synthetic, deliberately UNSORTED, with one all-blank row
    return pd.DataFrame({
        "Org Name": ["Zeta PD TX", "Alpha PD SC", "Alpha PD SC", "", "Mid PD NC"],
        "Search Time": ["03/30/2026, 01:00:00 PM", "03/30/2026, 09:00:00 AM",
                        "03/30/2026, 08:00:00 AM", "", "03/30/2026, 10:00:00 AM"],
        "Reason": ["Traffic - x", "Homicide - y", "Drugs - z", "", "Welfare - w"],
    })

def test_slice_is_deterministic_and_sorted_then_head_n():
    df = _frame()
    out1 = build_slice(df, n=3, sort_columns=["Org Name", "Search Time"])
    out2 = build_slice(df, n=3, sort_columns=["Org Name", "Search Time"])
    # deterministic: identical bytes on repeat
    assert out1.to_csv(index=False) == out2.to_csv(index=False)
    # all-blank row dropped; stable total-order sort then first 3
    assert list(out1["Org Name"]) == ["Alpha PD SC", "Alpha PD SC", "Mid PD NC"]
    # the two Alpha rows are tie-broken by Search Time ascending (08:00 before 09:00)
    assert list(out1["Search Time"])[:2] == ["03/30/2026, 08:00:00 AM",
                                             "03/30/2026, 09:00:00 AM"]

def test_drop_all_empty_rows_only_drops_fully_blank():
    df = pd.DataFrame({"a": ["x", "", " "], "b": ["", "", ""]})
    out = build_slice(df, n=10, drop_all_empty_rows=True)
    assert list(out["a"]) == ["x"]  # rows 2 and 3 are fully blank -> dropped

def test_non_positive_n_raises():
    df = _frame()
    # n=0 and n=-1 must fail fast, not silently yield head(0)/head(-1) slices.
    with pytest.raises(ValueError):
        build_slice(df, n=0)
    with pytest.raises(ValueError):
        build_slice(df, n=-1)
