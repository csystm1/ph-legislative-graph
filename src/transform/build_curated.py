"""Promote staging parquet + person TOMLs into curated parquet.

Curated artifacts are the sole input to Postgres load. Small enough to
commit (see .gitignore), so the load step is self-contained after a
clone — no need to re-run fetch + parse before `python -m src.load.load_postgres`.

Outputs (atomic):
    data/curated/bills.parquet
    data/curated/authorship.parquet
    data/curated/committees.parquet
    data/curated/legislators.parquet
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pandas as pd

from src.utils.config import CURATED_DIR, PERSON_DIR, STAGING_DIR
from src.utils.io import atomic_write_parquet
from src.utils.logger import get_logger

logger = get_logger(__name__)

_STAGING_TABLES = ("bills", "authorship", "committees")


def _format_full_name(person: dict) -> str:
    """Readable display name: First Middle Last Suffix."""
    parts = [
        (person.get("first_name") or "").strip(),
        (person.get("middle_name") or "").strip(),
        (person.get("last_name") or "").strip(),
        (person.get("name_suffix") or "").strip(),
    ]
    name = " ".join(p for p in parts if p)
    return name or person["id"]


def build_legislators() -> pd.DataFrame:
    rows: list[dict] = []
    for f in PERSON_DIR.glob("*.toml"):
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        rows.append({
            "legislator_id": d["id"],
            "full_name":     _format_full_name(d),
            "last_name":     d.get("last_name"),
            "first_name":    d.get("first_name"),
            "middle_name":   d.get("middle_name"),
            "aliases":       d.get("aliases") or None,
            "memberships":   json.dumps(
                d.get("memberships") or [],
                separators=(",", ":"),
            ),
        })
    logger.info("legislators: %d rows", len(rows))
    return pd.DataFrame(rows, columns=[
        "legislator_id", "full_name", "last_name", "first_name",
        "middle_name", "aliases", "memberships",
    ])


def promote_staging() -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for name in _STAGING_TABLES:
        src = STAGING_DIR / f"{name}.parquet"
        if not src.exists():
            raise FileNotFoundError(
                f"Staging parquet missing: {src}. "
                f"Run `python -m src.ingest.parse_toml` first."
            )
        out[name] = pd.read_parquet(src)
        logger.info("%s: %d rows", name, len(out[name]))
    return out


def run() -> None:
    logger.info("building curated parquet...")
    staging = promote_staging()
    legislators = build_legislators()

    atomic_write_parquet(staging["bills"],       CURATED_DIR / "bills.parquet")
    atomic_write_parquet(staging["authorship"],  CURATED_DIR / "authorship.parquet")
    atomic_write_parquet(staging["committees"],  CURATED_DIR / "committees.parquet")
    atomic_write_parquet(legislators,            CURATED_DIR / "legislators.parquet")

    logger.info("done.")


if __name__ == "__main__":
    run()