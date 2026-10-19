"""Co-authorship graph construction.

Edge model: two legislators are connected when they co-sponsor the same
bill. The primary sponsor of a bill is **not** connected to that bill's
co-authors — the primary role is a distinct legislative action from
co-sponsorship, and treating it as an edge would let filing volume
dominate graph structure (a legislator's primary-degree scales with how
many bills they file, not with coalition behavior), which would bias
Louvain toward clustering by filing patterns rather than by coalition.

Instead, primary filings and related scalars are recorded as node
attributes so the "solo filer" vs "coalition filer" signal stays
available downstream without distorting community detection. The cost
is that bills with a single co-author contribute zero edges; that is
accepted, and solo-filers are kept as isolated nodes so the complete
legislator set is preserved.

Both raw co-authorship count and Jaccard overlap are stored per edge.
"""

from __future__ import annotations

from itertools import combinations

import networkx as nx
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

_NODE_ATTRIBUTES = (
    "bills_primary_filed",
    "bills_coauthored",
    "bills_solo_filed",
    "bills_with_cosponsors_filed",
    "avg_coauthors_per_filed_bill",
    "coauthor_to_primary_ratio",
)


def build_coauthorship_graph(
    bills: pd.DataFrame,
    authorship: pd.DataFrame,
    chamber: str,
    congress: int,
) -> nx.Graph:
    """Build the co-authorship graph for one chamber-Congress slice."""
    scope_bills = bills[
        (bills["chamber"] == chamber) & (bills["congress"] == congress)
    ]
    scope_auth = authorship[authorship["bill_id"].isin(scope_bills["bill_id"])]

    coauthors = scope_auth[scope_auth["role"] == "co_author"]

    all_legislator_ids = set(scope_auth["legislator_id"].unique())
    bills_by_legislator: dict[str, set[str]] = {lid: set() for lid in all_legislator_ids}
    edge_counts: dict[tuple[str, str], int] = {}

    for bill_id, group in coauthors.groupby("bill_id"):
        authors = sorted(group["legislator_id"].unique())
        for a in authors:
            bills_by_legislator[a].add(bill_id)
        for a, b in combinations(authors, 2):
            edge_counts[(a, b)] = edge_counts.get((a, b), 0) + 1

    G = nx.Graph()
    G.add_nodes_from(all_legislator_ids)

    for (a, b), weight in edge_counts.items():
        union_size = len(bills_by_legislator[a] | bills_by_legislator[b])
        jaccard = weight / union_size if union_size > 0 else 0.0
        G.add_edge(a, b, weight=weight, jaccard=jaccard)

    add_node_attributes(G, scope_auth, bills_by_legislator)
    return G


def add_node_attributes(
    G: nx.Graph,
    scope_auth: pd.DataFrame,
    bills_by_legislator: dict[str, set[str]],
) -> None:
    """Attach the six node attributes from the design spec."""
    primaries = scope_auth[scope_auth["role"] == "primary"].copy()
    coauthors = scope_auth[scope_auth["role"] == "co_author"]

    n_coauthors_per_bill = coauthors.groupby("bill_id")["legislator_id"].nunique()
    primaries["n_coauthors"] = (
        primaries["bill_id"].map(n_coauthors_per_bill).fillna(0).astype(int)
    )

    primary_filed = primaries.groupby("legislator_id").size()
    solo = primaries[primaries["n_coauthors"] == 0].groupby("legislator_id").size()
    with_cosponsors = primaries[primaries["n_coauthors"] > 0].groupby("legislator_id").size()
    coauthor_slots = primaries.groupby("legislator_id")["n_coauthors"].sum()

    for node in G.nodes():
        pf = int(primary_filed.get(node, 0))
        ca = len(bills_by_legislator.get(node, set()))
        G.nodes[node].update(
            bills_primary_filed=pf,
            bills_coauthored=ca,
            bills_solo_filed=int(solo.get(node, 0)),
            bills_with_cosponsors_filed=int(with_cosponsors.get(node, 0)),
            avg_coauthors_per_filed_bill=(
                float(coauthor_slots.get(node, 0)) / pf if pf > 0 else None
            ),
            coauthor_to_primary_ratio=(ca / pf if pf > 0 else None),
        )


def build_edges_df(G: nx.Graph) -> pd.DataFrame:
    """Return a deterministic edge table with source <= target."""
    rows = [
        {
            "source": min(u, v),
            "target": max(u, v),
            "weight": d["weight"],
            "jaccard": d["jaccard"],
        }
        for u, v, d in G.edges(data=True)
    ]
    return (
        pd.DataFrame(rows, columns=["source", "target", "weight", "jaccard"])
        .sort_values(["source", "target"])
        .reset_index(drop=True)
    )


def build_nodes_df(G: nx.Graph, legislators: pd.DataFrame) -> pd.DataFrame:
    """Return a deterministic node table with attributes in fixed column order."""
    name_map = legislators.set_index("legislator_id")["full_name"].to_dict()
    rows = []
    for node, attrs in G.nodes(data=True):
        row = {"legislator_id": node, "full_name": name_map.get(node, node)}
        for attr in _NODE_ATTRIBUTES:
            row[attr] = attrs[attr]
        rows.append(row)
    cols = ["legislator_id", "full_name", *_NODE_ATTRIBUTES]
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values("legislator_id")
        .reset_index(drop=True)
    )