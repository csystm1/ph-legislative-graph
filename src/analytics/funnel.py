"""Committee funnel: bills assigned vs bills that became RAs, per committee.

Caveat: a bill assigned to N committees counts once in each. This is a
**committee touch rate**, not an exit rate — a high ratio means "bills
touching this committee often become law", not "this committee produces
laws". Documented in README.

Rankings are computed only within each (chamber, scope) cell and only
over committees with ``assigned_bills >= min_assignments``. Committees
below the floor get a null rank — a committee with 2 bills and 2 RAs
is noise, not a 100% funnel.
"""

from __future__ import annotations

import pandas as pd

_SCOPES: tuple = (17, 18, 19, "combined")
_MIN_ASSIGNMENTS = 10


def _aggregate(
    bills: pd.DataFrame,
    committees: pd.DataFrame,
    chamber: str,
    scope,
) -> pd.DataFrame:
    scope_bills = bills[bills["chamber"] == chamber]
    if scope != "combined":
        scope_bills = scope_bills[scope_bills["congress"] == scope]

    joined = committees.merge(
        scope_bills[["bill_id", "became_ra"]], on="bill_id", how="inner"
    )
    if joined.empty:
        return pd.DataFrame(
            columns=[
                "chamber",
                "scope",
                "committee_name",
                "assigned_bills",
                "ras",
                "rate",
            ]
        )

    agg = (
        joined.groupby("committee_name")
        .agg(
            assigned_bills=("bill_id", "nunique"),
            ras=("became_ra", "sum"),
        )
        .reset_index()
    )
    agg["assigned_bills"] = agg["assigned_bills"].astype(int)
    agg["ras"] = agg["ras"].astype(int)
    agg["rate"] = agg["ras"] / agg["assigned_bills"]
    agg.insert(0, "chamber", chamber)
    agg.insert(1, "scope", scope if scope == "combined" else str(scope))
    return agg


def build_committee_funnel(
    bills: pd.DataFrame,
    committees: pd.DataFrame,
    min_assignments: int = _MIN_ASSIGNMENTS,
) -> pd.DataFrame:
    frames = [
        _aggregate(bills, committees, chamber, scope)
        for chamber in sorted(bills["chamber"].unique())
        for scope in _SCOPES
    ]
    out = pd.concat(frames, ignore_index=True).copy()
    out["rank"] = None

    for _, grp in out.groupby(["chamber", "scope"], sort=False):
        eligible = grp["assigned_bills"] >= min_assignments
        if not eligible.any():
            continue
        ranks = grp.loc[eligible, "rate"].rank(method="min", ascending=False)
        out.loc[ranks.index, "rank"] = ranks

    return (
        out.sort_values(
            ["chamber", "scope", "assigned_bills"],
            ascending=[True, True, False],
        )
        .reset_index(drop=True)
    )