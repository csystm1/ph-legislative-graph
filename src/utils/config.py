"""Project path constants and runtime guards."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info < (3, 11):
    raise RuntimeError("Python >= 3.11 required (tomllib is stdlib from 3.11)")

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR     = PROJECT_ROOT / "data"
RAW_DIR      = DATA_DIR / "raw" / "open-congress-data"
STAGING_DIR  = DATA_DIR / "staging"
CURATED_DIR  = DATA_DIR / "curated"
OUTPUTS_DIR  = PROJECT_ROOT / "outputs"

COMMITTEE_DIR = RAW_DIR / "data" / "committee"
PERSON_DIR    = RAW_DIR / "data" / "person"
DOCUMENT_DIR  = RAW_DIR / "data" / "document"

CONGRESS_SCOPE = 19  # 19th Congress only