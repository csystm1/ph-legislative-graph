"""Project path constants and runtime guards."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise RuntimeError("Python >= 3.11 required (tomllib is stdlib from 3.11)")

# --------------------------------------------------------------------------- #
# Settings (Phase 3)
# --------------------------------------------------------------------------- #

import os as _os

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass


class Settings:
    """Environment-backed settings. Access is lazy so Phase 1/2 commands
    keep working without a .env file."""

    @property
    def postgres_uri(self) -> str:
        uri = _os.environ.get("DATABASE_URL")
        if not uri:
            raise RuntimeError(
                "DATABASE_URL is not set. Copy .env.example to .env "
                "and adjust the credentials if needed."
            )
        return uri


settings = Settings()

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw" / "open-congress-data"
STAGING_DIR  = DATA_DIR / "staging"
CURATED_DIR  = DATA_DIR / "curated"
OUTPUTS_DIR  = PROJECT_ROOT / "outputs"

HOUSE_COMMITTEE_MAP_PATH = DATA_DIR / "committee" / ".house-committee-mapping.yml"
COMMITTEE_DIR = RAW_DIR / "data" / "committee"
PERSON_DIR    = RAW_DIR / "data" / "person"
DOCUMENT_DIR  = RAW_DIR / "data" / "document"

CONGRESS_SCOPE = (17, 18, 19)  # Phase 2: 17th-19th Congress