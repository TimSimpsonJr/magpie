"""Build a small, deterministic, NEUTRAL slice of a large public CSV.

For Magpie's corpus/public/ "try it now" + CI golden source. The slice rule is
intentionally neutral (NO outcome tuning): a stable total-order sort, then the first
N rows. Anyone can re-run this against the source to reproduce the slice
byte-for-byte. See the Phase 11 design doc, section 3.2.
"""
from __future__ import annotations

import argparse
from typing import Sequence

import pandas as pd


def build_slice(
    df: pd.DataFrame,
    *,
    n: int,
    sort_columns: Sequence[str] | None = None,
    drop_all_empty_rows: bool = True,
) -> pd.DataFrame:
    """Return a deterministic first-N slice after a stable total-order sort.

    drop_all_empty_rows: the ONE permitted STRUCTURAL filter -- drop rows whose every
    cell is blank/NA/whitespace. Never an outcome-based filter.
    sort_columns: leading sort keys; ALL remaining columns are appended as
    deterministic tiebreakers so the order is total (fully reproducible). Defaults to
    file column order.
    n: rows to keep -- fixed for file size, NOT tuned to outputs. Must be > 0.
    """
    if n <= 0:
        raise ValueError("n must be > 0 (got %r); a non-positive slice size is a bug" % (n,))
    work = df.copy()
    if drop_all_empty_rows:
        nonblank = work.apply(
            lambda col: col.fillna("").astype(str).str.strip() != "", axis=0
        )
        work = work[nonblank.any(axis=1)]
    cols = list(work.columns)
    leading = [c for c in (sort_columns or []) if c in cols]
    order = leading + [c for c in cols if c not in leading]
    # Sort on a string view so mixed/blank dtypes order deterministically.
    key = work[order].fillna("").astype(str)
    work = work.loc[key.sort_values(by=list(order), kind="stable").index]
    return work.head(n).reset_index(drop=True)


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Build a deterministic public CSV slice.")
    ap.add_argument("source", help="path to the full source CSV (local; not committed)")
    ap.add_argument("out", help="path to write the slice CSV")
    ap.add_argument("-n", type=int, required=True, help="rows to keep (for file size)")
    ap.add_argument("--sort", nargs="*", default=None, help="leading sort columns")
    args = ap.parse_args(argv)
    df = pd.read_csv(args.source, dtype=str, keep_default_na=False, na_values=[""])
    sliced = build_slice(df, n=args.n, sort_columns=args.sort)
    sliced.to_csv(args.out, index=False)
    print(f"wrote {len(sliced)} rows -> {args.out}")


if __name__ == "__main__":
    main()
