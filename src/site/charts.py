"""Static PNG fallbacks for the graph pages and the temporal page.

Matplotlib renders the same layout as the interactive view so a reader on
mobile, on a no-JS device, or printing the site sees a consistent image.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend — must precede pyplot import
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
import pandas as pd  # noqa: E402

from src.site.theme import COMMUNITY_COLORS, COMMUNITY_FALLBACK  # noqa: E402

_MIN_SIZE = 20.0
_MAX_SIZE = 140.0


def _node_size(bills_coauthored: int) -> float:
    """Matplotlib sizes are area; the corresponding radius range is
    sqrt(20/π) ≈ 2.5 to sqrt(140/π) ≈ 6.7 — visually comparable to the
    interactive view's 6-22px diameter range.
    """
    if bills_coauthored <= 0:
        return _MIN_SIZE
    scaled = _MIN_SIZE + math.sqrt(bills_coauthored) * 7
    return min(scaled, _MAX_SIZE)


def _node_color(community_id: int) -> str:
    if 0 <= community_id < len(COMMUNITY_COLORS):
        return COMMUNITY_COLORS[community_id]
    return COMMUNITY_FALLBACK


def _save_atomic(fig, out_path: Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".part")
    # ``format="png"`` is required: the ".part" suffix would otherwise
    # confuse matplotlib's format inference and raise before writing.
    try:
        fig.savefig(tmp, format="png", bbox_inches="tight")
        os.replace(tmp, out_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _graph_from_frames(nodes: pd.DataFrame, edges: pd.DataFrame,
                       communities: pd.DataFrame) -> tuple[nx.Graph, pd.DataFrame]:
    merged = nodes.merge(communities, on="legislator_id", how="left")
    merged["community_id"] = merged["community_id"].fillna(-1).astype(int)
    G = nx.Graph()
    for lid in merged["legislator_id"]:
        G.add_node(lid)
    for _, e in edges.iterrows():
        G.add_edge(e["source"], e["target"])
    return G, merged.set_index("legislator_id")


def _draw_graph(ax, G: nx.Graph, merged_by_id: pd.DataFrame, *,
                title: str, top_labels: int = 0,
                fig_font: float = 8.0) -> None:
    ax.set_facecolor("#fafafa")

    if G.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "empty graph", ha="center", va="center")
        ax.set_axis_off()
        return

    pos = nx.spring_layout(G, seed=42, k=1.2, iterations=80)

    edge_x, edge_y = [], []
    for u, v in G.edges():
        if u in pos and v in pos:
            edge_x.extend([pos[u][0], pos[v][0], None])
            edge_y.extend([pos[u][1], pos[v][1], None])
    ax.plot(edge_x, edge_y, color="#888888", alpha=0.15, linewidth=0.5, zorder=1)

    nids = list(G.nodes())
    node_x = [pos[n][0] for n in nids]
    node_y = [pos[n][1] for n in nids]
    node_c = [_node_color(int(merged_by_id.loc[n, "community_id"])) for n in nids]
    node_s = [_node_size(int(merged_by_id.loc[n, "bills_coauthored"])) for n in nids]
    ax.scatter(node_x, node_y, c=node_c, s=node_s, edgecolors="#333333",
               linewidths=0.5, alpha=0.85, zorder=2)

    if top_labels > 0:
        label_rows = (
            merged_by_id.sort_values("bills_coauthored", ascending=False)
            .head(top_labels)
        )
        for lid, row in label_rows.iterrows():
            if lid in pos:
                x, y = pos[lid]
                ax.annotate(
                    str(row["full_name"]), (x, y),
                    fontsize=fig_font, ha="center", va="bottom",
                    xytext=(0, 5), textcoords="offset points",
                )

    ax.set_title(title, fontsize=10)
    ax.set_axis_off()


def render_graph_png(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    communities: pd.DataFrame,
    out_path: Path,
    *,
    title: str,
    top_labels: int = 10,
    top_edges: int | None = None,
) -> None:
    if top_edges is not None and top_edges < len(edges):
        edges = edges.nlargest(top_edges, "weight").reset_index(drop=True)
        connected = set(edges["source"]) | set(edges["target"])
        nodes = nodes[nodes["legislator_id"].isin(connected)].reset_index(drop=True)
        communities = communities[
            communities["legislator_id"].isin(connected)
        ].reset_index(drop=True)

    G, merged_by_id = _graph_from_frames(nodes, edges, communities)
    fig, ax = plt.subplots(figsize=(8, 6), dpi=130)
    fig.patch.set_facecolor("white")
    _draw_graph(ax, G, merged_by_id, title=title, top_labels=top_labels)
    fig.tight_layout()
    _save_atomic(fig, out_path)
    plt.close(fig)


def render_temporal_png(
    per_congress: list[tuple[int, pd.DataFrame, pd.DataFrame, pd.DataFrame]],
    out_path: Path,
    *,
    chamber: str,
    top_edges: int | None = None,
) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), dpi=130)
    fig.patch.set_facecolor("white")

    for ax, (congress, nodes, edges, communities) in zip(axes, per_congress):
        if top_edges is not None and top_edges < len(edges):
            edges = edges.nlargest(top_edges, "weight").reset_index(drop=True)
        # Drop isolated nodes so the panel is dominated by structure, not
        # by scattered grey dots. Isolated counts remain in the stats
        # table next to each panel.
        participating = set(edges["source"]) | set(edges["target"])
        nodes = nodes[nodes["legislator_id"].isin(participating)].reset_index(drop=True)
        communities = communities[
            communities["legislator_id"].isin(participating)
        ].reset_index(drop=True)

        G, merged_by_id = _graph_from_frames(nodes, edges, communities)
        _draw_graph(
            ax, G, merged_by_id,
            title=(
                f"{chamber.capitalize()} {congress}th — "
                f"{len(edges):,} edges, {len(nodes)} legislators"
            ),
            top_labels=0,
        )

    fig.suptitle(f"{chamber.capitalize()} co-authorship by Congress",
                 fontsize=12)
    fig.tight_layout()
    _save_atomic(fig, out_path)
    plt.close(fig)