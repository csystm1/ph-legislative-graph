"""Render the committee funnel page as static HTML.

Pre-rendered and JS-free: two chambers, four scopes each (17, 18, 19,
combined), sorted by rank within each scope, with below-floor committees
collapsed in a native ``<details>`` element. No client-side scripting —
the ranking floor already separates signal from noise.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Keep in sync with src.analytics.funnel._MIN_ASSIGNMENTS.
RANKING_FLOOR = 10

_TEMPLATE = Path(__file__).parent / "templates" / "funnel.html"
_SCOPES = ("17", "18", "19", "combined")


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_int(x) -> str:
    return f"{int(x):,}"


def _scope_label(scope: str) -> str:
    if scope == "combined":
        return "Combined (17th–19th Congress)"
    return f"{int(scope)}th Congress"


def _render_scope_table(df: pd.DataFrame) -> str:
    ranked = df[df["rank"].notna()].sort_values("rank").reset_index(drop=True)
    below = (
        df[df["rank"].isna()]
        .sort_values(["assigned_bills", "committee_name"], ascending=[False, True])
        .reset_index(drop=True)
    )

    ranked_rows = "".join(
        f'<tr><td class="num">{int(r["rank"])}</td>'
        f'<td>{r["committee_name"]}</td>'
        f'<td class="num">{_fmt_int(r["assigned_bills"])}</td>'
        f'<td class="num">{_fmt_int(r["ras"])}</td>'
        f'<td class="num">{_fmt_pct(r["rate"])}</td></tr>\n'
        for _, r in ranked.iterrows()
    )

    table = (
        "<table>\n"
        '<thead><tr>'
        '<th class="num">Rank</th>'
        "<th>Committee</th>"
        '<th class="num">Assigned</th>'
        '<th class="num">Became RA</th>'
        '<th class="num">Rate</th>'
        "</tr></thead>\n"
        f"<tbody>\n{ranked_rows}</tbody>\n"
        "</table>\n"
    )

    if len(below) > 0:
        below_rows = "".join(
            f'<tr><td class="num">—</td>'
            f'<td>{r["committee_name"]}</td>'
            f'<td class="num">{_fmt_int(r["assigned_bills"])}</td>'
            f'<td class="num">{_fmt_int(r["ras"])}</td>'
            f'<td class="num">{_fmt_pct(r["rate"])}</td></tr>\n'
            for _, r in below.iterrows()
        )
        table += (
            "<details>\n"
            f"<summary>{len(below)} committee(s) below the ranking floor "
            f"(fewer than {RANKING_FLOOR} assigned bills)</summary>\n"
            "<table>\n<tbody>\n"
            f"{below_rows}"
            "</tbody>\n</table>\n"
            "</details>\n"
        )
    return table


def _render_chamber_sections(funnel: pd.DataFrame, chamber: str) -> str:
    rows = funnel[funnel["chamber"] == chamber]
    parts: list[str] = []
    for scope in _SCOPES:
        sub = rows[rows["scope"].astype(str) == scope]
        if sub.empty:
            continue
        parts.append(f"<h3>{_scope_label(scope)}</h3>\n")
        parts.append(_render_scope_table(sub))
    return "".join(parts)


def render_funnel_body(funnel: pd.DataFrame) -> str:
    body = _TEMPLATE.read_text(encoding="utf-8")
    return (
        body.replace(
            "__HOUSE_SECTIONS__",
            _render_chamber_sections(funnel, "house"),
        ).replace(
            "__SENATE_SECTIONS__",
            _render_chamber_sections(funnel, "senate"),
        )
    )