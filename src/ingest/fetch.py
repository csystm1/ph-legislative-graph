"""Fetch or refresh the upstream open-congress-data sparse clone.

Idempotent:
- Missing raw dir         -> sparse clone (see handoff §3 of the project plan).
- Existing, HEAD == main  -> no-op.
- Existing, HEAD != main  -> git pull --depth 1.

Usage:
    python -m src.ingest.fetch             # skip if up-to-date
    python -m src.ingest.fetch --force     # always pull

Covers 17th-19th Congress bill directories plus person, committee, and
congress reference data. Existing clones from the 19th-only era need a
one-time `git sparse-checkout set` before re-running the parser.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from src.utils.config import RAW_DIR
from src.utils.logger import get_logger

logger = get_logger(__name__)

UPSTREAM_URL = "https://github.com/bettergovph/open-congress-data.git"
UPSTREAM_BRANCH = "main"

SPARSE_PATHS = [
    "data/document/hb/17",
    "data/document/hb/18",
    "data/document/hb/19",
    "data/document/sb/17",
    "data/document/sb/18",
    "data/document/sb/19",
    "data/person",
    "data/committee",
    "data/congress",
]


def _run(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    logger.debug("run: %s (cwd=%s)", " ".join(args), cwd)
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _sparse_clone(target: Path) -> None:
    if target.exists():
        raise RuntimeError(
            f"{target} exists but is not a git repo; "
            f"refusing to overwrite — remove it manually if that's what you want"
        )
    logger.info("sparse-cloning %s -> %s", UPSTREAM_URL, target)
    _run([
        "git", "clone",
        "--depth", "1",
        "--filter=blob:none",
        "--no-checkout",
        UPSTREAM_URL,
        str(target),
    ])
    _run(["git", "sparse-checkout", "init", "--cone"], cwd=target)
    _run(["git", "sparse-checkout", "set", *SPARSE_PATHS], cwd=target)
    _run(["git", "checkout", UPSTREAM_BRANCH], cwd=target)
    logger.info("sparse clone complete")


def _local_head(target: Path) -> str | None:
    try:
        r = _run(["git", "rev-parse", "HEAD"], cwd=target)
    except subprocess.CalledProcessError:
        return None
    return r.stdout.strip() or None


def _remote_head(target: Path) -> str | None:
    try:
        r = _run(
            ["git", "ls-remote", "origin", f"refs/heads/{UPSTREAM_BRANCH}"],
            cwd=target,
        )
    except subprocess.CalledProcessError:
        return None
    out = r.stdout.strip()
    return out.split()[0] if out else None


def _pull(target: Path) -> None:
    logger.info("pulling %s", target)
    _run(["git", "pull", "--depth", "1", "origin", UPSTREAM_BRANCH], cwd=target)


def fetch(*, force: bool = False) -> None:
    if not _is_git_repo(RAW_DIR):
        _sparse_clone(RAW_DIR)
        return

    if force:
        _pull(RAW_DIR)
        return

    local = _local_head(RAW_DIR)
    remote = _remote_head(RAW_DIR)
    if local and remote and local == remote:
        logger.info("upstream unchanged (HEAD=%s)", local[:10])
        return
    logger.info("upstream moved: %s -> %s",
                (local or "?")[:10], (remote or "?")[:10])
    _pull(RAW_DIR)


def run() -> None:
    """Default entry point: fetch if stale, skip if up-to-date."""
    fetch(force=False)


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description="Fetch/refresh upstream open-congress-data."
    )
    p.add_argument("--force", action="store_true", help="pull even if local == remote")
    args = p.parse_args()
    fetch(force=args.force)