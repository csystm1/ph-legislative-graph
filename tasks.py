"""Task runner for ph-legislative-graph.

Cross-platform replacement for a Makefile. Requires only Python (already
a project dependency) and Docker (already required for Postgres).

Usage:
    python tasks.py --help
    python tasks.py <task>
    python tasks.py pipeline        # full chain, stop on first failure
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from src.utils.config import PROJECT_ROOT
from src.utils.logger import get_logger

logger = get_logger(__name__)

ENV_FILE    = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# Postgres credentials. Keep in sync with .env.example / docker-compose.yml.
PG_USER = "phlg"
PG_DB   = "phlg"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _run(args: list[str], *, check: bool = True) -> int:
    logger.info("$ %s", " ".join(args))
    r = subprocess.run(args, check=False)
    if check and r.returncode != 0:
        raise SystemExit(r.returncode)
    return r.returncode


def _require_env() -> None:
    if not ENV_FILE.exists():
        raise SystemExit(
            f"Missing {ENV_FILE.name}. Run: cp {ENV_EXAMPLE.name} {ENV_FILE.name}"
        )


def _wait_for_pg(timeout_s: int = 30) -> None:
    logger.info("waiting for Postgres...")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres",
             "pg_isready", "-U", PG_USER, "-d", PG_DB],
            check=False, capture_output=True,
        )
        if r.returncode == 0:
            logger.info("Postgres ready.")
            return
        time.sleep(1)
    raise SystemExit(f"Postgres did not become healthy within {timeout_s}s")


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #

def cmd_fetch() -> None:
    _run([sys.executable, "-m", "src.ingest.fetch"])


def cmd_parse() -> None:
    _run([sys.executable, "-m", "src.ingest.parse_toml"])


def cmd_validate() -> None:
    _run([sys.executable, "-m", "src.validation.checks"])


def cmd_build_curated() -> None:
    _run([sys.executable, "-m", "src.transform.build_curated"])


def cmd_up() -> None:
    _require_env()
    _run(["docker", "compose", "up", "-d"])
    _wait_for_pg()


def cmd_down() -> None:
    _run(["docker", "compose", "down"])


def cmd_load() -> None:
    _require_env()
    _run([sys.executable, "-m", "src.load.load_postgres"])


def cmd_verify() -> None:
    sql = (
        "SELECT 'bills' AS table_name, COUNT(*) FROM bills "
        "UNION ALL SELECT 'legislators', COUNT(*) FROM legislators "
        "UNION ALL SELECT 'bill_authors', COUNT(*) FROM bill_authors "
        "UNION ALL SELECT 'bill_committees', COUNT(*) FROM bill_committees "
        "ORDER BY table_name"
    )
    _run(["docker", "compose", "exec", "-T", "postgres",
          "psql", "-U", PG_USER, "-d", PG_DB, "-c", sql])

def cmd_analytics() -> None:
    _run([sys.executable, "-m", "src.analytics.build_graph"])


def cmd_site() -> None:
    _run([sys.executable, "-m", "src.site.build_site"])


def cmd_psql() -> None:
    _run(["docker", "compose", "exec", "postgres",
          "psql", "-U", PG_USER, "-d", PG_DB])


def cmd_pipeline() -> None:
    for step in (cmd_fetch, cmd_parse, cmd_validate,
                 cmd_build_curated, cmd_up, cmd_load, cmd_verify):
        step()


def cmd_clean_staging() -> None:
    for p in sorted((PROJECT_ROOT / "data" / "staging").glob("*.parquet")):
        logger.info("rm %s", p)
        p.unlink()


def cmd_clean_curated() -> None:
    for p in sorted((PROJECT_ROOT / "data" / "curated").glob("*.parquet")):
        logger.info("rm %s", p)
        p.unlink()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

TASKS: dict[str, tuple[str, Callable[[], None]]] = {
    "fetch":         ("sparse-clone or refresh open-congress-data", cmd_fetch),
    "parse":         ("TOML -> data/staging/*.parquet",               cmd_parse),
    "validate":      ("pre-load gate; exit 0 = pass",                 cmd_validate),
    "build-curated": ("staging + person TOMLs -> data/curated/",      cmd_build_curated),
    "up":            ("docker compose up + wait for healthcheck",     cmd_up),
    "down":          ("docker compose down (keeps volume)",           cmd_down),
    "load":          ("curated parquet -> Postgres (idempotent)",     cmd_load),
    "verify":        ("row counts per table",                         cmd_verify),
    "analytics":     ("curated parquet -> data/analytics/ (graphs, communities, funnel)", cmd_analytics),
    "site":          ("data/analytics -> docs/ (static site for GitHub Pages)", cmd_site),
    "psql":          ("interactive psql shell",                       cmd_psql),
    "pipeline":      ("fetch -> parse -> validate -> build-curated -> up -> load -> verify", cmd_pipeline),
    "clean-staging": ("remove staging parquet",                       cmd_clean_staging),
    "clean-curated": ("remove curated parquet",                       cmd_clean_curated),
}


def main() -> int:
    epilog = "tasks:\n" + "\n".join(
        f"  {name:15s} {desc}" for name, (desc, _) in TASKS.items()
    )
    p = argparse.ArgumentParser(
        description="Task runner for ph-legislative-graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument("task", choices=sorted(TASKS), help="task to run (see below)")
    args = p.parse_args()
    TASKS[args.task][1]()
    return 0


if __name__ == "__main__":
    sys.exit(main())