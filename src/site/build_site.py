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

from src.site import charts, graphs
from src.site.funnel import render_funnel_body
from src.site.theme import CSS
from src.utils.config import CONGRESS_SCOPE, PROJECT_ROOT
from src.utils.io import atomic_write_text
from src.utils.logger import get_logger

logger = get_logger(__name__)

ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_ASSETS = DOCS_DIR / "assets"
DOCS_CSS = DOCS_ASSETS / "style.css"
DOCS_GRAPHS = DOCS_DIR / "graphs"
DOCS_PLOTS = DOCS_ASSETS / "plots"
DOCS_NOJEKYLL = DOCS_DIR / ".nojekyll"

TEMPLATE_DIR = Path(__file__).parent / "templates"

REPO_URL = "https://github.com/csystm1/ph-legislative-graph"
DATA_REPO_URL = "https://github.com/bettergovph/open-congress-data"

# Bill totals, asserted from Phase 1-3 verification.
_TOTAL_SENATE = 7_761
_TOTAL_HOUSE = 31_629
_TOTAL_BILLS = _TOTAL_SENATE + _TOTAL_HOUSE

_CHAMBERS = ("house", "senate")


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


def _wrap(*, title: str, description: str, body: str,
          active_nav: str, root: str, css_path: str,
          body_class: str = "") -> str:
    head = (
        _load("head.html")
        .replace("__PAGE_TITLE__", title)
        .replace("__PAGE_DESCRIPTION__", description)
        .replace("__CSS_PATH__", css_path)
        .replace("__BODY_CLASS__", body_class)
    )
    nav = _load("nav.html").replace("__ROOT__", root)
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


_HOUSE_BACKBONE_EDGES = 500


def _graph_view_section(chamber: str, slug: str,
                        total_edges: int, total_nodes: int) -> str:
    if chamber == "house":
        density = total_edges / (total_nodes * (total_nodes - 1) / 2) if total_nodes > 1 else 0
        return (
            "<section>\n"
            "<h2>Interactive backbone</h2>\n"
            "<aside>\n"
            f"Full House graph: <b>{total_edges:,} edges</b> across "
            f"{total_nodes} legislators — approximately "
            f"<b>{density:.0%} of all possible pairs</b> share "
            "co-authorship. No browser-side rendering of that density is "
            f"readable. Below is the <b>{_HOUSE_BACKBONE_EDGES} strongest "
            "co-authorship pairs</b> as a readable backbone; statistics "
            "table above reflects the full graph. Senate pages retain "
            "full-graph interactive views.\n"
            "</aside>\n"
            "<p>\n"
            "  Hover any node for the full attribute tooltip; drag to pan;\n"
            "  scroll to zoom (labels overlap at default zoom — zooming in\n"
            "  separates them).\n"
            "</p>\n"
            f'<iframe class="graph-iframe" src="./{slug}-pyvis.html" height="800"\n'
            f'        loading="lazy" title="{slug} co-authorship backbone"></iframe>\n'
            "</section>\n"
        )
    return (
        "<section>\n"
        "<h2>Interactive graph</h2>\n"
        "<p>\n"
        "  Hover any node for the full attribute tooltip; drag to pan; scroll to zoom.\n"
        "  All legislators are labeled; zoom in to separate overlapping names.\n"
        "</p>\n"
        f'<iframe class="graph-iframe" src="./{slug}-pyvis.html" height="800"\n'
        f'        loading="lazy" title="{slug} co-authorship graph"></iframe>\n'
        "</section>\n"
    )


def _static_caption(chamber: str) -> str:
    if chamber == "house":
        return (
            "Same backbone as the interactive view, rendered as a static "
            "image for print and no-JS readers. Top 20 nodes by "
            "<code>bills_coauthored</code> are labeled — with ~150-200 nodes "
            "in the backbone, labeling all would overlap on a static image. "
            "The interactive view above labels every node."
        )
    return (
        "Full graph, static rendering. Top 10 nodes by "
        "<code>bills_coauthored</code> are labeled. The interactive view "
        "above labels every node."
    )


def _sparsity_note(chamber: str, congress: int,
                   isolated_fraction: float, edges: int) -> str:
    if chamber == "senate" and congress == 17:
        return (
            '<p class="tagline">'
            "<b>Sparsity note.</b> Only 2 bills in the 17th Senate carried "
            "≥2 co-authors, producing the 4 graph edges shown here. Most "
            "nodes are isolated by construction. The graph is honest about "
            "that, not broken — see the README's Limitations section.</p>"
        )
    if chamber == "senate" and isolated_fraction >= 0.4:
        return (
            '<p class="tagline">'
            f"<b>Sparsity note.</b> {isolated_fraction:.0%} of nodes are "
            "isolated — Senate co-author data is sparse in this Congress. "
            "Isolated nodes are kept, not dropped.</p>"
        )
    return ""


def _graph_body(stats: pd.DataFrame, chamber: str, congress: int) -> str:
    row = stats[(stats.chamber == chamber) & (stats.congress == congress)].iloc[0]
    slug = f"{chamber}-{congress}"
    sparsity = _sparsity_note(
        chamber, congress,
        float(row["isolated_fraction"]), int(row["edges"]),
    )
    body = _load("graph.html")
    replacements = {
        "__CHAMBER_TITLE__": chamber.capitalize(),
        "__CONGRESS__": str(congress),
        "__SLUG__": slug,
        "__NODES__": f"{int(row['nodes']):,}",
        "__EDGES__": f"{int(row['edges']):,}",
        "__MODULARITY__": _fmt_float(row["modularity"]),
        "__COMMS_NT__": f"{int(row['n_communities_nontrivial'])}",
        "__COMMS_ALL__": f"{int(row['n_communities_all'])}",
        "__ISOLATED__": _fmt_pct(row["isolated_fraction"]),
        "__SPARSITY_NOTE__": sparsity,
        "__GRAPH_VIEW_SECTION__": _graph_view_section(
            chamber, slug, int(row["edges"]), int(row["nodes"]),
        ),
        "__STATIC_CAPTION__": _static_caption(chamber),
    }
    for k, v in replacements.items():
        body = body.replace(k, v)
    return body


def _write_graph_pages(stats: pd.DataFrame) -> None:
    DOCS_GRAPHS.mkdir(parents=True, exist_ok=True)
    DOCS_PLOTS.mkdir(parents=True, exist_ok=True)

    for chamber in _CHAMBERS:
        for congress in CONGRESS_SCOPE:
            slug = f"{chamber}-{congress}"
            nodes = pd.read_parquet(ANALYTICS_DIR / "nodes" / f"{slug}.parquet")
            edges = pd.read_parquet(ANALYTICS_DIR / "edges" / f"{slug}.parquet")
            communities = pd.read_parquet(
                ANALYTICS_DIR / "communities" / f"{slug}.parquet"
            )

            if chamber == "senate":
                pyvis_html, _ = graphs.render_graph_html(
                    nodes, edges, communities,
                )
                pyvis_path = DOCS_GRAPHS / f"{slug}-pyvis.html"
                atomic_write_text(pyvis_html, pyvis_path)
                logger.info("wrote %s", pyvis_path)
            else:
                # House: interactive backbone of the strongest edges only.
                # The full graph is a near-complete graph (~90% of pairs
                # share bills); no view of that density is readable.
                pyvis_html, n_rendered = graphs.render_graph_html(
                    nodes, edges, communities,
                    top_edges=_HOUSE_BACKBONE_EDGES,
                )
                pyvis_path = DOCS_GRAPHS / f"{slug}-pyvis.html"
                atomic_write_text(pyvis_html, pyvis_path)
                logger.info(
                    "wrote %s (%d of %d edges)",
                    pyvis_path, n_rendered, len(edges),
                )

            png_path = DOCS_PLOTS / f"{slug}.png"
            if chamber == "house":
                # Backbone rendering for House: 500 strongest edges only.
                # Full 40k-edge PNG is an indistinguishable blob.
                charts.render_graph_png(
                    nodes, edges, communities, png_path,
                    title=f"{chamber.capitalize()} {congress}th — backbone (top 500 edges)",
                    top_labels=20,
                    top_edges=500,
                )
            else:
                charts.render_graph_png(
                    nodes, edges, communities, png_path,
                    title=f"{chamber.capitalize()} {congress}th — co-authorship",
                    top_labels=10,
                )
            logger.info("wrote %s", png_path)

            body = _graph_body(stats, chamber, congress)
            page = _wrap(
                title=f"{chamber.capitalize()} {congress}th",
                description=(
                    f"Co-authorship graph of the {congress}th "
                    f"{chamber.capitalize()} of the Philippines."
                ),
                body=body,
                active_nav=slug,
                root="../",
                css_path="../assets/style.css",
                body_class="wide-view",
            )
            page_path = DOCS_GRAPHS / f"{slug}.html"
            atomic_write_text(page, page_path)
            logger.info("wrote %s", page_path)


def _metrics_table(stats: pd.DataFrame, chamber: str) -> str:
    rows = stats[stats.chamber == chamber].sort_values("congress")
    lines = [
        "      <table>",
        "        <thead><tr>",
        "          <th>Congress</th>",
        '          <th class="num">Nodes</th>',
        '          <th class="num">Edges</th>',
        '          <th class="num">Modularity</th>',
        '          <th class="num">Communities (n≥2)</th>',
        '          <th class="num">Isolated</th>',
        "        </tr></thead>",
        "        <tbody>",
    ]
    for _, r in rows.iterrows():
        lines.append(
            f"          <tr><td>{int(r['congress'])}th</td>"
            f'<td class="num">{int(r["nodes"]):,}</td>'
            f'<td class="num">{int(r["edges"]):,}</td>'
            f'<td class="num">{_fmt_float(r["modularity"])}</td>'
            f'<td class="num">{int(r["n_communities_nontrivial"])}</td>'
            f'<td class="num">{_fmt_pct(r["isolated_fraction"])}</td>'
            "</tr>"
        )
    lines.append("        </tbody>")
    lines.append("      </table>")
    return "\n".join(lines)


def _write_temporal_page(stats: pd.DataFrame) -> None:
    DOCS_PLOTS.mkdir(parents=True, exist_ok=True)

    for chamber in _CHAMBERS:
        per_congress = []
        for congress in CONGRESS_SCOPE:
            slug = f"{chamber}-{congress}"
            per_congress.append((
                congress,
                pd.read_parquet(ANALYTICS_DIR / "nodes" / f"{slug}.parquet"),
                pd.read_parquet(ANALYTICS_DIR / "edges" / f"{slug}.parquet"),
                pd.read_parquet(
                    ANALYTICS_DIR / "communities" / f"{slug}.parquet"
                ),
            ))
        png_path = DOCS_PLOTS / f"temporal-{chamber}.png"
        # House panels use the same 500-edge backbone as the House pages;
        # full-density rendering produces three indistinguishable blobs.
        # Senate panels keep full edges — sparse enough to read, and the
        # sparsity is the story.
        charts.render_temporal_png(
            per_congress, png_path, chamber=chamber,
            top_edges=_HOUSE_BACKBONE_EDGES if chamber == "house" else None,
        )
        logger.info("wrote %s", png_path)

    body = _load("temporal.html")
    body = body.replace("__HOUSE_METRICS_TABLE__", _metrics_table(stats, "house"))
    body = body.replace("__SENATE_METRICS_TABLE__", _metrics_table(stats, "senate"))
    page = _wrap(
        title="Temporal",
        description="Co-authorship across the 17th-19th Congress, per chamber.",
        body=body,
        active_nav="temporal",
        root="./",
        css_path="./assets/style.css",
        body_class="wide-view",
    )
    atomic_write_text(page, DOCS_DIR / "temporal.html")
    logger.info("wrote %s", DOCS_DIR / "temporal.html")


def _write_funnel_page(funnel_df: pd.DataFrame) -> None:
    body = render_funnel_body(funnel_df)
    page = _wrap(
        title="Committee funnel",
        description=(
            "Bills assigned to each committee and the fraction that became "
            "Republic Acts, per chamber and per Congress."
        ),
        body=body,
        active_nav="funnel",
        root="./",
        css_path="./assets/style.css",
        body_class="wide-view",
    )
    atomic_write_text(page, DOCS_DIR / "funnel.html")
    logger.info("wrote %s", DOCS_DIR / "funnel.html")


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
    logger.info("=== building site ===")
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

    DOCS_PLOTS.mkdir(parents=True, exist_ok=True)
    _ensure_nojekyll()
    _write_css()

    body = _render_landing(stats, funnel)
    page = _wrap(
        title="Overview",
        description=(
            "Co-authorship network and committee-funnel analysis of "
            "Philippine Senate and House bills, 17th-19th Congress (2016-2025)."
        ),
        body=body,
        active_nav="home",
        root="./",
        css_path="./assets/style.css",
    )
    atomic_write_text(page, DOCS_DIR / "index.html")
    logger.info("wrote %s", DOCS_DIR / "index.html")

    _write_graph_pages(stats)
    _write_temporal_page(stats)
    _write_funnel_page(funnel)

    logger.info("=== site build complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())