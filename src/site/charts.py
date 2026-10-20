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

from collections import Counter  # add near top imports


_PARTICLES = {"de", "dela", "del", "la", "las", "los", "di", "van", "von",
              "der", "den", "dos", "das", "y"}
_SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV", "V"}

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


def _surname_and_first(full_name: str) -> tuple[str, str]:
    """Return (first_token, surname) from full_name.

    Handles compound surnames via lowercase particles (dela Rosa, de Lima).
    Walks backward from the last token, absorbing any lowercase particle
    (``dela``, ``de``, ``del``, ``la``) into the surname. Capitalized middle
    tokens (Schramm, Martin, Macapagal) are treated as given names and
    dropped from the label — imperfect for capitalized compounds like
    ``Macapagal Arroyo`` but correct for every current 17th-19th senator.
    """
    if not full_name:
        return "", ""
    parts = full_name.split()
    while parts and parts[-1].upper().rstrip(".") in _SUFFIXES:
        parts = parts[:-1]
    if not parts:
        return "", ""
    first = parts[0]
    if len(parts) == 1:
        return first, ""
    surname_parts = [parts[-1]]
    i = len(parts) - 2
    while i > 0 and parts[i].lower() in _PARTICLES:
        surname_parts.insert(0, parts[i])
        i -= 1
    return first, " ".join(surname_parts)


def _short_label(full_name: str) -> str:
    """'Ronald dela Rosa' → 'Ronald dela Rosa'.

    'Aquilino Martin de la Llana Pimentel III' → 'Aquilino Pimentel'.
    Compound surnames preserved; middle names and suffixes dropped."""
    first, surname = _surname_and_first(full_name)
    if not first and not surname:
        return full_name or ""
    return f"{first} {surname}".strip() if surname else first


def _senate_labels(merged_by_id: pd.DataFrame, node_ids: list[str]) -> dict[str, str]:
    """First+Last labels for a Senate graph, collision-aware."""
    labels = {lid: _short_label(str(merged_by_id.loc[lid, "full_name"]))
              for lid in node_ids}
    counts = Counter(labels.values())
    collided = {lbl for lbl, c in counts.items() if c > 1}
    if collided:
        for lid, lbl in labels.items():
            if lbl in collided:
                full = str(merged_by_id.loc[lid, "full_name"])
                first, surname = _surname_and_first(full)
                mid_parts = full.split()[1:-1] if len(full.split()) > 2 else []
                mid_initial = next(
                    (p[0] for p in mid_parts
                     if p and p[0].isupper() and p not in _SUFFIXES),
                    "",
                )
                labels[lid] = (
                    f"{first} {mid_initial}. {surname}" if mid_initial
                    else f"{first} {surname}"
                )
    return labels


def _draw_graph(ax, G: nx.Graph, merged_by_id: pd.DataFrame, *,
                title: str, top_labels: int = 0,
                fig_font: float = 8.0,
                layout: str = "spring",
                all_labels: dict[str, str] | None = None) -> None:
    ax.set_facecolor("#fafafa")

    if G.number_of_nodes() == 0:
        ax.text(0.5, 0.5, "empty graph", ha="center", va="center")
        ax.set_axis_off()
        return

    if layout == "circular":
        pos = nx.circular_layout(G)
    else:
        pos = nx.spring_layout(G, seed=42, k=1.2, iterations=80)

    edge_alpha = 0.15
    edge_lw = 0.35 if layout == "circular" else 0.5

    edge_x, edge_y = [], []
    for u, v in G.edges():
        if u in pos and v in pos:
            edge_x.extend([pos[u][0], pos[v][0], None])
            edge_y.extend([pos[u][1], pos[v][1], None])
    ax.plot(edge_x, edge_y, color="#888888", alpha=edge_alpha,
            linewidth=edge_lw, zorder=1)

    nids = list(G.nodes())
    node_x = [pos[n][0] for n in nids]
    node_y = [pos[n][1] for n in nids]
    node_c = [_node_color(int(merged_by_id.loc[n, "community_id"])) for n in nids]
    node_s = [_node_size(int(merged_by_id.loc[n, "bills_coauthored"])) for n in nids]
    ax.scatter(node_x, node_y, c=node_c, s=node_s, edgecolors="#333333",
               linewidths=0.5, alpha=0.85, zorder=2)

    if all_labels is not None:
        # All-nodes labeling for circular layout: place text outside the
        # ring, with horizontal alignment chosen by quadrant so labels
        # never cross the ring into the graph interior.
        for lid in nids:
            if lid not in all_labels or lid not in pos:
                continue
            x, y = pos[lid]
            r = math.hypot(x, y) or 1.0
            ux, uy = x / r, y / r
            lx, ly = x + 0.10 * ux, y + 0.10 * uy
            angle = math.atan2(y, x)
            if -math.pi / 4 <= angle <= math.pi / 4:
                ha, va = "left", "center"
            elif math.pi / 4 < angle <= 3 * math.pi / 4:
                ha, va = "center", "bottom"
            elif angle > 3 * math.pi / 4 or angle < -3 * math.pi / 4:
                ha, va = "right", "center"
            else:
                ha, va = "center", "top"
            ax.annotate(all_labels[lid], (lx, ly), fontsize=fig_font,
                        ha=ha, va=va, zorder=3)
        ax.set_xlim(-1.35, 1.35)
        ax.set_ylim(-1.35, 1.35)
    elif top_labels > 0:
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
    chamber: str | None = None,
) -> None:
    if top_edges is not None and top_edges < len(edges):
        edges = edges.nlargest(top_edges, "weight").reset_index(drop=True)
        connected = set(edges["source"]) | set(edges["target"])
        nodes = nodes[nodes["legislator_id"].isin(connected)].reset_index(drop=True)
        communities = communities[
            communities["legislator_id"].isin(connected)
        ].reset_index(drop=True)

    G, merged_by_id = _graph_from_frames(nodes, edges, communities)

    if chamber == "senate":
        # Dense near-clique — circular layout, all nodes labeled.
        all_labels = _senate_labels(merged_by_id, list(G.nodes()))
        fig, ax = plt.subplots(figsize=(10, 10), dpi=140)
        fig.patch.set_facecolor("white")
        _draw_graph(ax, G, merged_by_id, title=title, top_labels=0,
                    fig_font=7.5, layout="circular", all_labels=all_labels)
    else:
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