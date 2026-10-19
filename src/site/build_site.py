"""Assemble the GitHub Pages site under docs/.

Phase 5, commit 1: landing page only. Later commits add the six graph
pages, the funnel table, and the temporal small-multiples.

Templates live in ``src/site/templates/*.html``. Substitution is plain
``str.replace`` on ``__PLACEHOLDER__`` tokens — no template engine. At
this scale (9 pages, small fixed set of substitutions) a Jinja-like
dependency adds more surface than it removes.

All writes are atomic (via src.utils.io) and idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.site.theme import CSS
from src.utils.config import PROJECT_ROOT
from src.utils.io import atomic_write_text
from src.utils.logger import get_logger

logger = get_logger(__name__)

ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_ASSETS = DOCS_DIR / "assets"
DOCS_CSS = DOCS_ASSETS / "style.css"
DOCS_NOJEKYLL = DOCS_DIR / ".nojekyll"

TEMPLATE_DIR = Path(__file__).parent / "templates"

REPO_URL = "https://github.com/csystm1/ph-legislative-graph"
DATA_REPO_URL = "https://github.com/bettergovph/open-congress-data"

# Bill totals, asserted from Phase 1-3 verification. Update here and
# re-verify against data/analytics/graph_stats.csv if the upstream ingest
# ever changes the corpus.
_TOTAL_SENATE = 7_761
_TOTAL_HOUSE = 31_629
_TOTAL_BILLS = _TOTAL_SENATE + _TOTAL_HOUSE

_CSS_PATH = "./assets/style.css"
_ROOT = "./"


def _load(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _fmt_float(x: float, digits: int = 3) -> str:
    return f"{x:.{digits}f}"


def _graph_row(row: pd.Series) -> str:
    slug = f"{row['chamber']}-{row['congress']}"
    return (
        f"      <tr>"
        f"<td>{row['chamber'].capitalize()}</td>"
        f"<td>{row['congress']}th</td>"
        f'<td class="num">{int(row["nodes"]):,}</td>'
        f'<td class="num">{int(row["edges"]):,}</td>'
        f'<td class="num">{_fmt_float(row["modularity"])}</td>'
        f'<td class="num">{int(row["n_communities_nontrivial"])}</td>'
        f'<td class="num">{_fmt_pct(row["isolated_fraction"])}</td>'
        f'<td><a href="./graphs/{slug}.html">view graph</a></td>'
        f"</tr>\n"
    )


def _render_landing(stats: pd.DataFrame, funnel: pd.DataFrame) -> str:
    def row(chamber: str, congress: int) -> pd.Series:
        return stats[(stats.chamber == chamber) & (stats.congress == congress)].iloc[0]

    h17, h18, h19 = row("house", 17), row("house", 18), row("house", 19)
    s17, s18, s19 = row("senate", 17), row("senate", 18), row("senate", 19)

    house_mods = [h17["modularity"], h18["modularity"], h19["modularity"]]
    house_nodes_range = (
        f"{int(min(h17['nodes'], h18['nodes'], h19['nodes']))}–"
        f"{int(max(h17['nodes'], h18['nodes'], h19['nodes']))}"
    )
    house_edges_range = (
        f"{int(min(h17['edges'], h18['edges'], h19['edges'])):,}–"
        f"{int(max(h17['edges'], h18['edges'], h19['edges'])):,}"
    )

    f17 = funnel[(funnel.chamber == "house") & (funnel.scope.astype(str) == "17")]
    rules = f17[f17.committee_name == "Rules"].iloc[0]
    apps = f17[f17.committee_name == "Appropriations"].iloc[0]

    body = _load("landing.html")
    replacements = {
        "__TOTAL_BILLS__": f"{_TOTAL_BILLS:,}",
        "__TOTAL_SENATE__": f"{_TOTAL_SENATE:,}",
        "__TOTAL_HOUSE__": f"{_TOTAL_HOUSE:,}",
        "__HOUSE_NODES__": house_nodes_range,
        "__HOUSE_EDGES__": house_edges_range,
        "__HOUSE_MOD_MIN__": _fmt_float(min(house_mods)),
        "__HOUSE_MOD_MAX__": _fmt_float(max(house_mods)),
        "__S17_EDGES__": f"{int(s17['edges']):,}",
        "__S17_ISOLATED__": _fmt_pct(s17["isolated_fraction"]),
        "__S19_EDGES__": f"{int(s19['edges']):,}",
        "__S19_ISOLATED__": _fmt_pct(s19["isolated_fraction"]),
        "__S18_MOD__": _fmt_float(s18["modularity"]),
        "__S18_EDGES__": f"{int(s18['edges']):,}",
        "__S18_NODES__": f"{int(s18['nodes']):,}",
        "__H17_RULES_RATE__": _fmt_pct(rules["rate"]),
        "__H17_RULES_N__": f"{int(rules['assigned_bills']):,}",
        "__H17_APPS_RATE__": _fmt_pct(apps["rate"]),
        "__H17_APPS_N__": f"{int(apps['assigned_bills']):,}",
        "__GRAPH_ROWS__": "".join(_graph_row(r) for _, r in stats.iterrows()),
        "__REPO_URL__": REPO_URL,
        "__DATA_REPO_URL__": DATA_REPO_URL,
    }
    for key, val in replacements.items():
        body = body.replace(key, val)
    return body


def _wrap_page(*, title: str, description: str, body: str, active_nav: str) -> str:
    head = (
        _load("head.html")
        .replace("__PAGE_TITLE__", title)
        .replace("__PAGE_DESCRIPTION__", description)
        .replace("__CSS_PATH__", _CSS_PATH)
    )
    nav = _load("nav.html").replace("__ROOT__", _ROOT)
    if active_nav:
        nav = nav.replace(
            f'data-nav="{active_nav}"',
            f'data-nav="{active_nav}" class="active"',
        )
    footer = (
        _load("footer.html")
        .replace("__REPO_URL__", REPO_URL)
        .replace("__DATA_REPO_URL__", DATA_REPO_URL)
    )
    return f"{head}{nav}{body}{footer}"


def _write_css() -> None:
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    atomic_write_text(CSS, DOCS_CSS)
    logger.info("wrote %s", DOCS_CSS)


def _ensure_nojekyll() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    if not DOCS_NOJEKYLL.exists():
        atomic_write_text("", DOCS_NOJEKYLL)
        logger.info("wrote %s (empty)", DOCS_NOJEKYLL)


def main() -> int:
    logger.info("=== building site (landing only) ===")
    for p in (
        ANALYTICS_DIR / "graph_stats.csv",
        ANALYTICS_DIR / "committee_funnel.csv",
    ):
        if not p.exists():
            raise FileNotFoundError(
                f"{p} missing — run 'python tasks.py analytics' first"
            )

    stats = pd.read_csv(ANALYTICS_DIR / "graph_stats.csv")
    funnel = pd.read_csv(ANALYTICS_DIR / "committee_funnel.csv")

    _ensure_nojekyll()
    _write_css()

    body = _render_landing(stats, funnel)
    page = _wrap_page(
        title="Overview",
        description=(
            "Co-authorship network and committee-funnel analysis of "
            "Philippine Senate and House bills, 17th-19th Congress (2016-2025)."
        ),
        body=body,
        active_nav="home",
    )
    out = DOCS_DIR / "index.html"
    atomic_write_text(page, out)
    logger.info("wrote %s", out)

    logger.info("=== site build complete (landing only) ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())