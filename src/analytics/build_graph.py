"""Orchestrator: read curated parquet, write Phase 4 analytics artifacts.

All outputs under ``data/analytics/`` and committed to git:

    edges/{chamber}-{congress}.parquet
    nodes/{chamber}-{congress}.parquet
    communities/{chamber}-{congress}.parquet
    graph_stats.csv
    committee_funnel.csv

Six graphs total: {house, senate} x {17, 18, 19}, kept separate so
temporal and cross-chamber comparisons stay honest — coalition structure
in the 17th Senate is not assumed comparable to the 19th House.
"""

from __future__ import annotations

import pandas as pd

from src.analytics.communities import detect_communities
from src.analytics.funnel import build_committee_funnel
from src.analytics.graph import (
    build_coauthorship_graph,
    build_edges_df,
    build_nodes_df,
)
from src.utils.config import CONGRESS_SCOPE, CURATED_DIR, PROJECT_ROOT
from src.utils.io import atomic_write_csv, atomic_write_parquet
from src.utils.logger import get_logger

logger = get_logger(__name__)

ANALYTICS_DIR = PROJECT_ROOT / "data" / "analytics"
_CHAMBERS = ("house", "senate")


def _process_one(
    bills: pd.DataFrame,
    authorship: pd.DataFrame,
    legislators: pd.DataFrame,
    chamber: str,
    congress: int,
) -> dict:
    G = build_coauthorship_graph(bills, authorship, chamber, congress)
    edges_df = build_edges_df(G)
    nodes_df = build_nodes_df(G, legislators)
    communities_df, stats = detect_communities(G)

    slug = f"{chamber}-{congress}"
    atomic_write_parquet(edges_df, ANALYTICS_DIR / "edges" / f"{slug}.parquet")
    atomic_write_parquet(nodes_df, ANALYTICS_DIR / "nodes" / f"{slug}.parquet")
    atomic_write_parquet(
        communities_df, ANALYTICS_DIR / "communities" / f"{slug}.parquet"
    )

    n_nodes = G.number_of_nodes()
    n_isolated = sum(1 for n in G.nodes() if G.degree(n) == 0)
    isolated_fraction = n_isolated / n_nodes if n_nodes > 0 else 0.0

    logger.info(
        "%s nodes=%d edges=%d modularity=%s comms_all=%d comms_nontrivial=%d isolated=%.3f",
        slug,
        n_nodes,
        G.number_of_edges(),
        stats["modularity"],
        stats["n_communities_all"],
        stats["n_communities_nontrivial"],
        isolated_fraction,
    )

    return {
        "chamber": chamber,
        "congress": congress,
        "nodes": n_nodes,
        "edges": G.number_of_edges(),
        "modularity": stats["modularity"],
        "n_communities_all": stats["n_communities_all"],
        "n_communities_nontrivial": stats["n_communities_nontrivial"],
        "isolated_fraction": isolated_fraction,
    }


def main() -> int:
    logger.info("=== Phase 4: graph build, communities, committee funnel ===")

    bills = pd.read_parquet(CURATED_DIR / "bills.parquet")
    authorship = pd.read_parquet(CURATED_DIR / "authorship.parquet")
    legislators = pd.read_parquet(CURATED_DIR / "legislators.parquet")
    committees = pd.read_parquet(CURATED_DIR / "committees.parquet")

    stats_rows = [
        _process_one(bills, authorship, legislators, chamber, congress)
        for chamber in _CHAMBERS
        for congress in CONGRESS_SCOPE
    ]
    stats_df = pd.DataFrame(
        stats_rows,
        columns=[
            "chamber",
            "congress",
            "nodes",
            "edges",
            "modularity",
            "n_communities_all",
            "n_communities_nontrivial",
            "isolated_fraction",
        ],
    )
    atomic_write_csv(stats_df, ANALYTICS_DIR / "graph_stats.csv")

    funnel_df = build_committee_funnel(bills, committees)
    atomic_write_csv(funnel_df, ANALYTICS_DIR / "committee_funnel.csv")

    logger.info("=== Phase 4 complete ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())