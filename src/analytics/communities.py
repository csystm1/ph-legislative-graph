"""Louvain community detection on the co-authorship graphs."""

from __future__ import annotations

import community as community_louvain  # python-louvain package
import networkx as nx
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SEED = 42


def detect_communities(
    G: nx.Graph, seed: int = DEFAULT_SEED
) -> tuple[pd.DataFrame, dict]:
    """Run Louvain and return (per-node assignment df, graph-level stats dict).

    Stats keys: modularity (None on a graph with no edges), n_communities_all
    (includes singleton communities from isolated nodes), n_communities_nontrivial
    (size >= 2 only).
    """
    if G.number_of_nodes() == 0:
        return (
            pd.DataFrame(columns=["legislator_id", "community_id"]),
            {
                "modularity": None,
                "n_communities_all": 0,
                "n_communities_nontrivial": 0,
            },
        )

    partition = community_louvain.best_partition(
        G, weight="weight", random_state=seed
    )

    if G.number_of_edges() > 0:
        modularity = float(community_louvain.modularity(partition, G, weight="weight"))
    else:
        modularity = 0.0

    sizes: dict[int, int] = {}
    for comm in partition.values():
        sizes[comm] = sizes.get(comm, 0) + 1

    df = (
        pd.DataFrame(
            [{"legislator_id": n, "community_id": c} for n, c in partition.items()]
        )
        .sort_values("legislator_id")
        .reset_index(drop=True)
    )

    return df, {
        "modularity": modularity,
        "n_communities_all": len(sizes),
        "n_communities_nontrivial": sum(1 for s in sizes.values() if s >= 2),
    }