"""Render a co-authorship graph parquet set to a pyvis HTML document.

Coordinates come from ``networkx.spring_layout(seed=42)`` and are pinned
on the nodes; physics is disabled so the graph does not wobble on load.
The palette and node/edge styling are set in ``theme.py`` and this
module respectively — the intent is a static, paper-figure look rather
than a tool default.

``top_edges`` filters the rendered graph to the N highest-weight edges
and to nodes participating in those edges. Used for House graphs, where
the full edge set is a near-complete graph and not viewable.

Output is a complete HTML document, meant to be embedded in an iframe
from the per-graph page.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

import networkx as nx
import pandas as pd
from pyvis.network import Network

from src.site.theme import CHART_BG, COMMUNITY_COLORS, COMMUNITY_FALLBACK

_MIN_NODE_SIZE = 6
_MAX_NODE_SIZE = 22
_LAYOUT_SIZE = (1600.0, 1100.0)


def _node_size(bills_coauthored: int) -> int:
    """Scale node radius gently — the intention is a readable graph,
    not a bubble chart. sqrt gives diminishing growth, and the cap keeps
    the highest-degree nodes from dominating the layout.
    """
    if bills_coauthored <= 0:
        return _MIN_NODE_SIZE
    scaled = _MIN_NODE_SIZE + math.sqrt(bills_coauthored) * 0.9
    return int(min(scaled, _MAX_NODE_SIZE))


def _node_color(community_id: int) -> str:
    if 0 <= community_id < len(COMMUNITY_COLORS):
        return COMMUNITY_COLORS[community_id]
    return COMMUNITY_FALLBACK


def _label_font_size(n_nodes: int) -> int:
    """Autoscale label font so labels fit but stay legible.

    Reader can zoom to separate overlapping labels; the intent is to
    reveal all names, not to pre-filter to a curated subset.
    """
    if n_nodes <= 50:
        return 12
    if n_nodes <= 150:
        return 10
    return 9


def _fmt_opt(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if isinstance(x, float):
        return f"{x:.2f}"
    return str(x)


def _tooltip(row: pd.Series) -> str:
    return (
        f"{row['full_name']}\n"
        f"community: {int(row['community_id'])}\n"
        f"primary filed: {int(row['bills_primary_filed'])}\n"
        f"co-authored: {int(row['bills_coauthored'])}\n"
        f"solo filed: {int(row['bills_solo_filed'])}\n"
        f"with cosponsors: {int(row['bills_with_cosponsors_filed'])}\n"
        f"avg cosponsors/filed: {_fmt_opt(row['avg_coauthors_per_filed_bill'])}\n"
        f"coauthor/primary ratio: {_fmt_opt(row['coauthor_to_primary_ratio'])}"
    )


def render_graph_html(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    communities: pd.DataFrame,
    *,
    height_px: int = 800,
    top_edges: int | None = None,
) -> tuple[str, int]:
    """Render pyvis HTML. Returns (html, n_edges_rendered)."""
    if top_edges is not None and top_edges < len(edges):
        edges = edges.nlargest(top_edges, "weight").reset_index(drop=True)
        participating = set(edges["source"]) | set(edges["target"])
        nodes = nodes[nodes["legislator_id"].isin(participating)].reset_index(drop=True)
        communities = communities[
            communities["legislator_id"].isin(participating)
        ].reset_index(drop=True)

    merged = nodes.merge(communities, on="legislator_id", how="left")
    merged["community_id"] = merged["community_id"].fillna(-1).astype(int)
    merged_by_id = merged.set_index("legislator_id")

    n_rendered = len(edges)

    G = nx.Graph()
    for lid in merged["legislator_id"]:
        G.add_node(lid)
    for _, e in edges.iterrows():
        G.add_edge(e["source"], e["target"])

    pos = nx.spring_layout(G, seed=42, k=1.2, iterations=80) if G.number_of_nodes() else {}

    xs = [p[0] for p in pos.values()] or [0.0]
    ys = [p[1] for p in pos.values()] or [0.0]
    x_min, x_span = min(xs), (max(xs) - min(xs)) or 1.0
    y_min, y_span = min(ys), (max(ys) - min(ys)) or 1.0
    w, h = _LAYOUT_SIZE

    def _scale(p):
        x, y = p
        return (
            (x - x_min) / x_span * w - w / 2,
            (y - y_min) / y_span * h - h / 2,
        )

    # cdn_resources="remote": load vis.js from CDN rather than inlining it
    # (~600 KB per page otherwise). The PNG fallback means the static
    # rendering is usable without JS or network access.
    net = Network(
        height=f"{height_px}px",
        width="100%",
        bgcolor=CHART_BG,
        font_color="#222",
        directed=False,
        notebook=False,
        cdn_resources="remote",
    )

    label_font = _label_font_size(len(merged))
    options = {
        "physics": {"enabled": False},
        "configure": {"enabled": False, "filter": False},
        "interaction": {
            "hover": True,
            "hoverConnectedEdges": False,
            "navigationButtons": False,
            "multiselect": False,
            "keyboard": {"enabled": False},
        },
        "edges": {
            "smooth": False,
            "color": {"opacity": 0.22},
            "width": 0.8,
        },
        "nodes": {
            "borderWidth": 1,
            "font": {"size": label_font, "face": "sans-serif", "color": "#222"},
        },
    }
    net.set_options(json.dumps(options))

    # All nodes are labeled — the reader can zoom to separate overlaps.
    # Hover still provides the full attribute tooltip regardless of label.
    for lid in G.nodes():
        row = merged_by_id.loc[lid]
        x, y = _scale(pos[lid])
        color = _node_color(int(row["community_id"]))
        net.add_node(
            lid,
            label=str(row["full_name"]),
            title=_tooltip(row),
            color={
                "background": color,
                "border": "#333333",
                "highlight": {"background": color, "border": "#000000"},
            },
            size=_node_size(int(row["bills_coauthored"])),
            x=float(x),
            y=float(y),
        )

    # Tooltips only on strong edges — otherwise the JSON payload is huge
    # for information edge thickness already encodes.
    for _, e in edges.iterrows():
        weight = int(e["weight"])
        kwargs = {"value": weight}
        if weight >= 5:
            kwargs["title"] = (
                f"shared bills: {weight}\njaccard: {e['jaccard']:.3f}"
            )
        net.add_edge(e["source"], e["target"], **kwargs)

    # pyvis 0.3.x exposes ``write_html`` rather than ``generate_html``.
    fd, tmp_path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    try:
        net.write_html(tmp_path, notebook=False)
        html = Path(tmp_path).read_text(encoding="utf-8")
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
    return html, n_rendered