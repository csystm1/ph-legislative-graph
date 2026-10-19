"""Load curated parquet into PostgreSQL with upsert semantics.

Idempotent: reruns converge to the same table state. Schema (sql/schema.sql)
is applied on every run — CREATE TABLE IF NOT EXISTS, so schema edits only
need a re-run, not a volume wipe.

Load order respects FK constraints:
    legislators -> bills -> bill_authors -> bill_committees
"""
from __future__ import annotations

import json
import sys

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, text
from sqlalchemy import literal_column
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.utils.config import CURATED_DIR, PROJECT_ROOT, settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
CHUNK_SIZE = 500

# (table_name, pk_columns, update_columns). update_columns = all non-PK
# columns in contract order; must match sql/schema.sql exactly.

# (postgres_table, parquet_basename, pk_cols, update_cols). The parquet
# basename differs from the table name for bill_authors / bill_committees
# because build_curated keeps staging's filenames.
TABLES = [
    (
        "legislators", "legislators",
        ["legislator_id"],
        ["full_name", "last_name", "first_name", "middle_name",
         "aliases", "memberships"],
    ),
    (
        "bills", "bills",
        ["bill_id"],
        ["chamber", "congress", "bill_number", "title", "long_title",
         "filed_date", "status", "became_ra"],
    ),
    (
        "bill_authors", "authorship",
        ["bill_id", "legislator_id", "role"],
        ["resolution_method"],
    ),
    (
        "bill_committees", "committees",
        ["bill_id", "committee_name"],
        ["committee_type"],
    ),
]


# --------------------------------------------------------------------------- #
# Type preparation
# --------------------------------------------------------------------------- #

def _to_date_or_none(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def _prepare(table: str, df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if table == "bills" and "filed_date" in df.columns:
        df["filed_date"] = df["filed_date"].apply(_to_date_or_none)
    if table == "legislators" and "memberships" in df.columns:
        # JSON-string parquet -> native Python list/dict for SQLAlchemy JSONB.
        df["memberships"] = df["memberships"].apply(
            lambda s: json.loads(s) if isinstance(s, str) else s
        )
    return df


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

def _split_sql(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";") if s.strip()]


def _apply_schema(engine) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for stmt in _split_sql(sql):
            conn.execute(text(stmt))
    logger.info("schema applied from %s", SCHEMA_PATH)


# --------------------------------------------------------------------------- #
# Upsert
# --------------------------------------------------------------------------- #

def _upsert_table(
    engine, table_name: str, pk_cols: list[str],
    update_cols: list[str], df: pd.DataFrame,
) -> tuple[int, int]:
    all_cols = [*pk_cols, *update_cols]
    missing = [c for c in all_cols if c not in df.columns]
    if missing:
        raise ValueError(f"{table_name}: curated parquet missing columns {missing}")
    records = df[all_cols].to_dict(orient="records")

    metadata = MetaData()
    inserted = updated = 0
    with engine.begin() as conn:
        tbl = Table(table_name, metadata, autoload_with=conn)
        before = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()

        for start in range(0, len(records), CHUNK_SIZE):
            chunk = records[start:start + CHUNK_SIZE]
            stmt = pg_insert(tbl).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=[tbl.c[c] for c in pk_cols],
                set_={c: stmt.excluded[c] for c in update_cols},
            ).returning(literal_column("(xmax = 0)").label("inserted"))

            results = conn.execute(stmt).scalars().all()
            chunk_inserted = sum(1 for r in results if r)
            inserted += chunk_inserted
            updated += len(results) - chunk_inserted

        after = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()

    logger.info(
        "%s: %d rows -> inserted %d, updated %d, before %d, after %d (delta %+d)",
        table_name, len(records), inserted, updated, before, after, after - before,
    )
    if inserted + updated != len(records):
        raise RuntimeError(
            f"{table_name}: processed {inserted + updated}, expected {len(records)}"
        )
    return inserted, updated


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

def load() -> None:
    engine = create_engine(settings.postgres_uri, future=True)
    _apply_schema(engine)

    for table_name, parquet_name, pk_cols, update_cols in TABLES:
        src = CURATED_DIR / f"{parquet_name}.parquet"
        if not src.exists():
            raise FileNotFoundError(
                f"Curated parquet missing: {src}. "
                f"Run `python -m src.transform.build_curated` first."
            )
        df = _prepare(table_name, pd.read_parquet(src))
        _upsert_table(engine, table_name, pk_cols, update_cols, df)

    logger.info("done.")


def main() -> int:
    try:
        load()
    except Exception:
        logger.exception("load failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())