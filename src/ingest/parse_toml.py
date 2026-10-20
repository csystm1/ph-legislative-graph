"""Parse 19th-Congress bill TOMLs into staging parquet.

Outputs (atomic):
    data/staging/bills.parquet
    data/staging/authorship.parquet
    data/staging/committees.parquet

Diagnostics (gitignored):
    outputs/house_unresolved_coauthors.csv
    outputs/senate_unresolved_coauthors.csv
    outputs/senate_coauthor_mismatches.csv
    outputs/senate_primary_disagreements.csv
    outputs/senate_not_found.csv
    outputs/house_0000_sentinel_bills.csv
    outputs/house_committee_missing_codes.csv

Senate committees come from the [[committees]] block; codes → YAML → TOML
name is the fallback for the ~0.5% without a block. House committees come
from meta.congress_website_committee_codes → hand-encoded YAML
(data/committee/.house-committee-mapping.yml). Codes not in the YAML fall
back to the raw code as display name and are counted in
outputs/house_committee_missing_codes.csv.
"""
from __future__ import annotations

import collections
import re
import time
import tomllib
from pathlib import Path

import pandas as pd
import yaml

from src.ingest.entity_resolution import (
    PersonIndexes,
    build_person_indexes,
    resolve_house_coauthor,
    resolve_senate_coauthor,
)
from src.utils.config import (
    COMMITTEE_DIR,
    CONGRESS_SCOPE,
    DOCUMENT_DIR,
    HOUSE_COMMITTEE_MAP_PATH,
    OUTPUTS_DIR,
    PERSON_DIR,
    STAGING_DIR,
)
from src.utils.io import atomic_write_csv, atomic_write_parquet
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Explicit column lists so empty frames from missing Congresses still
# concat cleanly with populated ones.
_BILLS_COLUMNS = ["bill_id", "chamber", "congress", "bill_number", "title",
                  "long_title", "filed_date", "status", "became_ra"]
_AUTHORSHIP_COLUMNS = ["bill_id", "legislator_id", "role", "resolution_method"]
_COMMITTEES_COLUMNS = ["bill_id", "committee_name", "committee_type"]

_PROGRESS_EVERY = 2000  # emit a progress line every N files within a chamber dir

# --- History parsing: pure string walk, no regex --------------------------
#
# History actions are semicolon-terminated, ALL-CAPS-named prose. Regexes
# here are landmines: `(?:[A-Z][A-Z.'\-]*\s*)+` followed by `.*?` nested
# lazy quantifiers explode on any action that has "Letter from Senator"
# but doesn't match "intention to co-author of SBN-N" cleanly. Two attempts
# at fixing the regex backfired; string-walk is O(n) and provably correct.
#
# Heuristic: a name token ends the walk when we hit a "prose word" — a word
# starting with lowercase and length >= 4. Short lowercase particles ("de",
# "la", "van") are preserved. Trailing punctuation is stripped.

def _extract_name_from(action: str, start_pos: int) -> tuple[str, int]:
    """Walk a name starting at ``start_pos``; return (name, end_position)."""
    pos = start_pos
    end_of_name = start_pos
    while pos < len(action):
        tok_end = pos
        while tok_end < len(action) and action[tok_end] not in " \t":
            tok_end += 1
        word = action[pos:tok_end]
        stripped = word.strip(".,;:()")
        if not stripped:
            break
        # Prose token: lowercase start AND 4+ chars. End of name.
        if stripped[0].islower() and len(stripped) >= 4:
            break
        pos = tok_end
        end_of_name = pos
        while pos < len(action) and action[pos] in " \t":
            pos += 1
    name = action[start_pos:end_of_name].strip(" .,;:()")
    return name, end_of_name

# --- Senate co-author extraction: pattern dispatch ------------------------
#
# Senate history encodes co-authorship across many phrasings in 17th-19th
# Congresses. Extraction dispatches on action shape; each branch walks the
# string. No regex (see note above _extract_name_from).

_PROSE_WORDS = frozenset({
    "was", "were", "is", "are", "be", "to", "will", "shall",
    "had", "has", "have",
})

_NAME_PARTICLES = frozenset({
    "de", "la", "del", "las", "los", "van", "von", "der", "den",
    "di", "da", "dos", "das",
})

_TITLE_PREFIXES = (
    "Senate President Pro Tempore ",
    "Senate President ",
    "Senators ",
    "Senator ",
)

# Ground-truth bills for the self-check. Key: (congress, bill_number).
# Value: minimum distinct coauthor names expected from [[history]] extraction
# (before entity resolution — resolution failures are accepted and logged).
_SELF_CHECK_EXPECTATIONS = {
    (17, 2124): 4,
    (17, 1391): 6,
    (17, 1597): 3,
    (17, 1280): 5,
    (18, 1086): 4,
    (18, 2272): 10,
    (19, 2568): 7,
    (19, 2218): 6,
    (19, 1480): 5,
}


def _has_coauthor_word(s: str) -> bool:
    """True if s contains 'coauthor' / 'co-author' / 'co author' (any spacing)."""
    compact = "".join(c for c in s if c.isalpha()).lower()
    return "coauthor" in compact


def _find_sbn(action: str, from_pos: int = 0) -> tuple[int | None, int]:
    """Find first SBN-N (N digits) at/after from_pos; return (N, end_pos)."""
    idx = action.find("SBN-", from_pos)
    if idx == -1:
        return None, -1
    num_start = idx + 4
    num_end = num_start
    while num_end < len(action) and action[num_end].isdigit():
        num_end += 1
    if num_end == num_start:
        return None, -1
    return int(action[num_start:num_end]), num_end


def _extract_allcaps_name_list_from(action: str, start_pos: int) -> tuple[list[str], int]:
    """Walk a comma/and-separated ALL-CAPS name list from start_pos."""
    names: list[str] = []
    pos = start_pos
    name_start = pos
    name_end = pos
    while pos < len(action):
        tok_end = pos
        while tok_end < len(action) and action[tok_end] not in " \t":
            tok_end += 1
        word = action[pos:tok_end]
        stripped = word.strip(".,;:()")
        if not stripped:
            # Advance at least one char, or we spin forever when pos
            # lands on a space (tok_end == pos → empty word → no progress).
            pos = tok_end if tok_end > pos else pos + 1
            continue
        is_separator = stripped.lower() in ("and", "or")
        is_prose = (
            stripped[0].islower()
            and len(stripped) >= 4
            and not is_separator
        )
        if is_prose:
            break
        if is_separator:
            if name_end > name_start:
                nm = action[name_start:name_end].strip(" .,;:()")
                if nm:
                    names.append(nm)
            name_start = tok_end
            while name_start < len(action) and action[name_start] in " \t":
                name_start += 1
            name_end = name_start
            pos = tok_end if tok_end > pos else pos + 1
            continue
        name_end = tok_end
        if word.endswith((",", ";", ":")):
            nm = action[name_start:name_end].strip(" .,;:()")
            if nm:
                names.append(nm)
            name_start = tok_end
            while name_start < len(action) and action[name_start] in " \t":
                name_start += 1
            name_end = name_start
        pos = tok_end
        while pos < len(action) and action[pos] in " \t":
            pos += 1
    if name_end > name_start:
        nm = action[name_start:name_end].strip(" .,;:()")
        if nm:
            names.append(nm)
    return names, name_end


def _clean_titled_piece(piece: str) -> str:
    """Strip title prefixes and truncate at first lowercase prose word."""
    piece = piece.strip(" .,;:()")
    for prefix in _TITLE_PREFIXES:
        if piece.startswith(prefix):
            piece = piece[len(prefix):].strip(" .,;:()")
            break
    words = piece.split()
    for i, w in enumerate(words):
        w_clean = w.strip(".,;:()")
        if (
            w_clean
            and w_clean[0].islower()
            and len(w_clean) >= 3
            and w_clean.lower() not in _NAME_PARTICLES
        ):
            words = words[:i]
            break
    return " ".join(words)


def _extract_titled_name_list_from(action: str, start_pos: int) -> tuple[list[str], int]:
    """Walk a comma/and-separated Title Case name list from start_pos."""
    end = len(action)
    for i in range(start_pos, len(action)):
        if action[i] == ";":
            end = i
            break
    body = action[start_pos:end]
    body = body.replace(" and ", ", ")
    pieces = [p.strip() for p in body.split(",") if p.strip()]
    names = [_clean_titled_piece(p) for p in pieces]
    names = [n for n in names if n]
    return names, end


def _extract_name_list_any(action: str, start_pos: int) -> tuple[list[str], int]:
    """Dispatch ALL-CAPS vs Title Case list walker on the first token."""
    tok_end = start_pos
    while tok_end < len(action) and action[tok_end] not in " \t":
        tok_end += 1
    word = action[start_pos:tok_end].strip(".,;:()")
    if not word:
        return [], start_pos
    if word.isupper():
        return _extract_allcaps_name_list_from(action, start_pos)
    return _extract_titled_name_list_from(action, start_pos)


def _strip_trailing_verb(name: str) -> str:
    changed = True
    while changed:
        changed = False
        for tail in (" was", " were", " is", " are", " be", " to", " will", " shall", " on"):
            if name.endswith(tail):
                name = name[:-len(tail)].rstrip()
                changed = True
                break
    return name


def _extract_coauthors_from_action(action: str) -> list[str]:
    """Return coauthor raw names from one history action; [] if none."""
    if not _has_coauthor_word(action):
        return []

    # ---- Letter from Senator: dispatch on the suffix clause ----
    if "Letter from Senator " in action:
        m = "informing that Senators "
        idx = action.find(m)
        if idx != -1:
            extracted, _ = _extract_name_list_any(action, idx + len(m))
            return extracted
        m = "requesting that Senator "
        idx = action.find(m)
        if idx != -1:
            nm, _ = _extract_name_from(action, idx + len(m))
            return [_strip_trailing_verb(nm)] if nm else []
        m = "Letter from Senator "
        idx = action.find(m)
        if idx != -1:
            nm, _ = _extract_name_from(action, idx + len(m))
            return [_strip_trailing_verb(nm)] if nm else []
        return []

    # ---- Passive plural: "Senators X, Y, Z were made coauthors" ----
    if action.startswith("Senators "):
        extracted, _ = _extract_name_list_any(action, len("Senators "))
        return extracted

    # ---- Passive singular: "Senator X was made co-author" ----
    if action.startswith("Senator ") and " was made " in action:
        nm, _ = _extract_name_from(action, len("Senator "))
        nm = _strip_trailing_verb(nm)
        return [nm] if nm else []

    # ---- Motion: "Motion of Senator X [that ... | namely ... | together with ...]" ----
    m = "Motion of Senator "
    idx = action.find(m)
    if idx != -1:
        names: list[str] = []
        nm_x, nm_x_end = _extract_name_from(action, idx + len(m))
        nm_x = _strip_trailing_verb(nm_x)
        if nm_x:
            names.append(nm_x)
        namely_idx = action.find("namely ", nm_x_end)
        if namely_idx != -1:
            extracted, _ = _extract_name_list_any(action, namely_idx + len("namely "))
            names.extend(extracted)
        tw = "together with Senators "
        tw_idx = action.find(tw, nm_x_end)
        if tw_idx != -1:
            extracted, _ = _extract_name_list_any(action, tw_idx + len(tw))
            names.extend(extracted)
        else:
            tw_sg = "together with Senator "
            tw_sg_idx = action.find(tw_sg, nm_x_end)
            if tw_sg_idx != -1:
                nm_y, _ = _extract_name_from(action, tw_sg_idx + len(tw_sg))
                nm_y = _strip_trailing_verb(nm_y)
                if nm_y:
                    names.append(nm_y)
        return names

    # ---- Bulk: "All Senators present were made coauthors: [list]" ----
    if "All Senators present" in action:
        m = "were made coauthors: "
        idx = action.find(m)
        if idx != -1:
            list_start = idx + len(m)
            if action[list_start:list_start + len("Senators ")] == "Senators ":
                list_start += len("Senators ")
            extracted, _ = _extract_name_list_any(action, list_start)
            return extracted
        return []

    # ---- Manifestation of another: "Manifestation of Senator X that Senator Y ..." ----
    if "Manifestation of Senator " in action:
        m = "that Senator "
        idx = action.find(m)
        if idx != -1:
            nm_y, _ = _extract_name_from(action, idx + len(m))
            nm_y = _strip_trailing_verb(nm_y)
            return [nm_y] if nm_y else []
        return []

    # ---- Manifestation by: "Senator X manifested [that ... | the desire of Senator Y ...]" ----
    if action.startswith("Senator ") and " manifested " in action:
        nm_x, nm_x_end = _extract_name_from(action, len("Senator "))
        nm_x = _strip_trailing_verb(nm_x)
        if not nm_x:
            return []
        m = "desire of Senator "
        idx = action.find(m, nm_x_end)
        if idx != -1:
            nm_y, _ = _extract_name_from(action, idx + len(m))
            nm_y = _strip_trailing_verb(nm_y)
            return [nm_y] if nm_y else []
        return [nm_x]

    return []


def _senate_coauthors_from_history(d: dict, bill_number: int) -> list[tuple[str, int]]:
    """[(raw_name, sbn)] from all observed co-author phrasings in [[history]]."""
    out: list[tuple[str, int]] = []
    for h in d.get("history") or []:
        action = h.get("action") or ""
        if not _has_coauthor_word(action):
            continue
        sbn, _ = _find_sbn(action)
        if sbn is None:
            sbn = bill_number
        for name in _extract_coauthors_from_action(action):
            out.append((name, sbn))
    return out

_RE_RA_MENTION = re.compile(
    r"(?:Republic Act\s+No\.?\s+|RA\s+No\.?\s+|R\.A\.\s+No\.?\s+)(\d+)",
    re.IGNORECASE,
)


# --- Small helpers --------------------------------------------------------

def _senate_primary_from_authors_raw(authors_raw) -> str | None:
    """First author from authors_raw.

    Some bills store a single string, others a list of
    ``"Last, First M."`` strings. Handle both.
    """
    if not authors_raw:
        return None

    def _first_pair(s: str) -> str | None:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) < 2:
            return None
        return f"{parts[0]}, {parts[1]}"

    if isinstance(authors_raw, list):
        if not authors_raw:
            return None
        first = authors_raw[0]
        return _first_pair(first) if isinstance(first, str) else None

    if isinstance(authors_raw, str):
        if not authors_raw.strip():
            return None
        return _first_pair(authors_raw)

    logger.warning("unexpected authors_raw type: %s", type(authors_raw).__name__)
    return None


def _senate_primary_from_history(d: dict) -> str | None:
    """First senator from ``Introduced by Senator(s) ...;`` in [[history]]."""
    for h in d.get("history") or []:
        action = h.get("action") or ""
        for marker in ("Introduced by Senator ", "Introduced by Senators "):
            idx = action.find(marker)
            if idx == -1:
                continue
            name, _ = _extract_name_from(action, idx + len(marker))
            if not name:
                continue
            # Multi-author: "A, B and C" → take first
            for sep in (" and ", " AND ", " & "):
                if sep in name:
                    name = name.split(sep)[0].strip()
                    break
            if name:
                return name
    return None


def _check_became_ra(d: dict) -> bool:
    """Conservative: True only if a history action explicitly names an RA number."""
    for h in d.get("history") or []:
        if _RE_RA_MENTION.search(h.get("action") or ""):
            return True
    return False


def _first_status(d: dict) -> str | None:
    block = d.get("status") or []
    if not block:
        return None
    if len(block) > 1:
        logger.debug("bill has %d status entries; using first", len(block))
    return (block[0] or {}).get("status")


# --- Committee map loading ------------------------------------------------

def _load_yaml_flat(path: Path) -> dict:
    """Load a YAML expected to be flat ``{code: ulid}``.

    Defensive: if the file is nested-by-congress ``{13: {CODE: ULID}}``,
    flatten it (collision → last wins, logged).
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not raw:
        return {}
    if all(isinstance(v, dict) for v in raw.values()):
        flat: dict = {}
        collisions = 0
        for _congress, inner in raw.items():
            for k, v in (inner or {}).items():
                if k in flat and flat[k] != v:
                    collisions += 1
                flat[k] = v
        if collisions:
            logger.warning(
                "%s was nested-by-congress with %d cross-congress code collisions",
                path.name, collisions,
            )
        return flat
    return raw


def _load_committee_names(committee_dir: Path) -> dict[str, str]:
    """ULID → display name from ``data/committee/<ULID>.toml``."""
    names: dict[str, str] = {}
    for f in committee_dir.glob("*.toml"):
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        if "id" in d and "name" in d:
            names[d["id"]] = d["name"]
    return names


# --- Main parse -----------------------------------------------------------

def parse_bills(
    *,
    congress: int,
    house_indexes: PersonIndexes,
    senate_indexes: PersonIndexes,
    senate_committee_map: dict[str, str],
    house_committee_map: dict[str, str],
    committee_names_by_ulid: dict[str, str],
) -> dict:
    bills: list[dict] = []
    authorship: list[dict] = []
    committees: list[dict] = []

    senate_unresolved: collections.Counter[str] = collections.Counter()
    house_unresolved: collections.Counter[str] = collections.Counter()
    senate_coauthor_mismatches: list[dict] = []
    senate_primary_disagreements: list[dict] = []
    senate_not_found: list[dict] = []
    house_0000: list[dict] = []
    house_committee_missing: collections.Counter[str] = collections.Counter()
    committee_fallback_used = 0
    committee_block_used = 0

    # --- Senate ---
    # --- Senate ---
    sb_dir = DOCUMENT_DIR / "sb" / str(congress)
    sb_files = sorted(sb_dir.glob("*.toml"))
    sb_total = len(sb_files)
    sb_start = time.monotonic()
    logger.info("  sb/%d: parsing %d files", congress, sb_total)
    for i, f in enumerate(sb_files, 1):
        if i % _PROGRESS_EVERY == 0 or i == sb_total:
            elapsed = time.monotonic() - sb_start
            rate = i / elapsed if elapsed > 0 else 0.0
            logger.info(
                "  sb/%d: %d/%d (%.1f%%) %.0fs, %.1f files/s",
                congress, i, sb_total, 100.0 * i / sb_total, elapsed, rate,
            )
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        m = d.get("meta") or {}

        if m.get("not_found"):
            senate_not_found.append({"file": f.name, "congress": congress})
            continue

        bill_number = m.get("bill_number")
        if bill_number is None:
            logger.warning("senate bill missing bill_number: %s", f.name)
            continue

        if m.get("congress") is not None and m["congress"] != congress:
            logger.warning(
                "senate congress mismatch %s: meta=%s, dir=%s (using dir)",
                f.name, m["congress"], congress,
            )
        bill_id = f"SBN-{bill_number:05d}-{congress}"
        title = (m.get("title") or "").strip() or (m.get("long_title") or "").strip()
        if not title:
            title = f"Untitled {bill_id}"
            logger.warning("no title for %s", bill_id)

        bills.append({
            "bill_id": bill_id,
            "chamber": "senate",
            "congress": congress,
            "bill_number": bill_number,
            "title": title,
            "long_title": m.get("long_title"),
            "filed_date": m.get("date_filed"),
            "status": _first_status(d),
            "became_ra": _check_became_ra(d),
        })

        # --- Senate primary ---
        sources: dict[str, str] = {}

        ar_name = _senate_primary_from_authors_raw(m.get("authors_raw"))
        if ar_name:
            pid, _method = resolve_senate_coauthor(ar_name, senate_indexes)
            if pid:
                sources["authors_raw"] = pid

        hist_name = _senate_primary_from_history(d)
        if hist_name:
            pid, _method = resolve_senate_coauthor(hist_name, senate_indexes)
            if pid:
                sources["history_introducer"] = pid

        codes = list(m.get("senate_website_author_codes") or [])
        if codes:
            pid = senate_indexes.senate_key_index.get(codes[0])
            if pid:
                sources["code_0"] = pid

        primary_pid = None
        primary_method = None
        for source, method in (
            ("authors_raw", "senate_authors_raw"),
            ("history_introducer", "senate_history_introducer"),
            ("code_0", "senate_author_code_0"),
        ):
            if source in sources:
                primary_pid = sources[source]
                primary_method = method
                break

        if len(set(sources.values())) > 1:
            senate_primary_disagreements.append({
                "bill_id": bill_id,
                "authors_raw": sources.get("authors_raw"),
                "history_introducer": sources.get("history_introducer"),
                "code_0": sources.get("code_0"),
                "chosen": primary_pid,
                "method": primary_method,
            })

        if primary_pid:
            authorship.append({
                "bill_id": bill_id,
                "legislator_id": primary_pid,
                "role": "primary",
                "resolution_method": primary_method,
            })

        # --- Senate co-authors ---
        extracted = _senate_coauthors_from_history(d, bill_number)
        if (congress, bill_number) in _SELF_CHECK_EXPECTATIONS:
            want = _SELF_CHECK_EXPECTATIONS[(congress, bill_number)]
            if len(extracted) < want:
                logger.warning(
                    "self-check: %dth SBN-%d yielded %d coauthor(s), expected >=%d",
                    congress, bill_number, len(extracted), want,
                )
        seen_pids: set[str] = {primary_pid} if primary_pid else set()
        for raw_name, sbn_in_action in extracted:
            if sbn_in_action != bill_number:
                senate_coauthor_mismatches.append({
                    "bill_id": bill_id,
                    "file_bill_number": bill_number,
                    "action_sbn": sbn_in_action,
                    "raw_name": raw_name,
                })
            pid, method = resolve_senate_coauthor(raw_name, senate_indexes)
            if not pid:
                senate_unresolved[raw_name] += 1
                continue
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            authorship.append({
                "bill_id": bill_id,
                "legislator_id": pid,
                "role": "co_author",
                "resolution_method": f"senate_history/{method}",
            })

        # --- Senate committees ---
        block = d.get("committees") or []
        if block:
            committee_block_used += 1
            for c in block:
                name = (c.get("name") or "").strip()
                if not name:
                    continue
                committees.append({
                    "bill_id": bill_id,
                    "committee_name": name,
                    "committee_type": c.get("type"),
                })
        else:
            used = False
            for code in m.get("senate_website_committee_codes") or []:
                ulid = senate_committee_map.get(code)
                if not ulid:
                    continue
                name = committee_names_by_ulid.get(ulid)
                if not name:
                    continue
                committees.append({
                    "bill_id": bill_id,
                    "committee_name": name,
                    "committee_type": None,
                })
                used = True
            if used:
                committee_fallback_used += 1

    # --- House ---
    # --- House ---
    hb_dir = DOCUMENT_DIR / "hb" / str(congress)
    hb_files = sorted(hb_dir.glob("*.toml"))
    hb_total = len(hb_files)
    hb_start = time.monotonic()
    logger.info("  hb/%d: parsing %d files", congress, hb_total)
    for i, f in enumerate(hb_files, 1):
        if i % _PROGRESS_EVERY == 0 or i == hb_total:
            elapsed = time.monotonic() - hb_start
            rate = i / elapsed if elapsed > 0 else 0.0
            logger.info(
                "  hb/%d: %d/%d (%.1f%%) %.0fs, %.1f files/s",
                congress, i, hb_total, 100.0 * i / hb_total, elapsed, rate,
            )
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        m = d.get("meta") or {}

        if m.get("not_found"):
            continue

        bill_number = m.get("bill_number")
        if bill_number is None:
            logger.warning("house bill missing bill_number: %s", f.name)
            continue

        if m.get("congress") is not None and m["congress"] != congress:
            logger.warning(
                "house congress mismatch %s: meta=%s, dir=%s (using dir)",
                f.name, m["congress"], congress,
            )
        bill_id = f"HBN-{bill_number:05d}-{congress}"
        title = (
            (m.get("title") or "").strip()
            or (m.get("congress_website_title") or "").strip()
            or (m.get("long_title") or "").strip()
        )
        if not title:
            title = f"Untitled {bill_id}"
            logger.warning("no title for %s", bill_id)

        bills.append({
            "bill_id": bill_id,
            "chamber": "house",
            "congress": congress,
            "bill_number": bill_number,
            "title": title,
            "long_title": m.get("long_title"),
            "filed_date": m.get("date_filed"),
            "status": _first_status(d),
            "became_ra": _check_became_ra(d),
        })

        # --- House primary ---
        codes_raw = list(m.get("congress_website_author_codes") or [])
        codes_dedup = list(dict.fromkeys(codes_raw))  # order-preserving dedup
        primary_pid = None
        primary_method = None
        if codes_dedup:
            c0 = codes_dedup[0]
            if c0 and c0 != "0000":
                pid = house_indexes.house_author_key_index.get(c0)
                if pid:
                    primary_pid = pid
                    primary_method = "house_author_code_0"
        if not primary_pid:
            house_0000.append({
                "bill_id": bill_id,
                "author_codes_head": codes_dedup[:5],
            })
        else:
            authorship.append({
                "bill_id": bill_id,
                "legislator_id": primary_pid,
                "role": "primary",
                "resolution_method": primary_method,
            })

        # --- House co-authors ---
        seen_pids = {primary_pid} if primary_pid else set()
        for raw_name in m.get("congress_website_coauthors_raw") or []:
            pid, method = resolve_house_coauthor(raw_name, codes_raw, house_indexes)
            if not pid:
                house_unresolved[raw_name] += 1
                continue
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            authorship.append({
                "bill_id": bill_id,
                "legislator_id": pid,
                "role": "co_author",
                "resolution_method": f"house/{method}",
            })

        # --- House committees ---
        house_codes = list(dict.fromkeys(m.get("congress_website_committee_codes") or []))
        seen_names: set[str] = set()
        for code in house_codes:
            name = house_committee_map.get(code)
            if not name:
                house_committee_missing[code] += 1
                name = code  # fallback: keep the row, flag for later resolution
            if name in seen_names:
                continue  # PK is (bill_id, committee_name); no dup rows
            seen_names.add(name)
            committees.append({
                "bill_id": bill_id,
                "committee_name": name,
                "committee_type": None,  # no primary/secondary on House bills
            })

    house_committee_rows = sum(1 for c in committees if c["bill_id"].startswith("HBN-"))
    logger.info(
        "committees: senate_block=%d, senate_fallback=%d, house_rows=%d, total=%d",
        committee_block_used, committee_fallback_used, house_committee_rows, len(committees),
    )

    return {
        "bills": pd.DataFrame(bills, columns=_BILLS_COLUMNS),
        "authorship": pd.DataFrame(authorship, columns=_AUTHORSHIP_COLUMNS),
        "committees": pd.DataFrame(committees, columns=_COMMITTEES_COLUMNS),
        "senate_unresolved": senate_unresolved,
        "house_unresolved": house_unresolved,
        "senate_coauthor_mismatches": senate_coauthor_mismatches,
        "senate_primary_disagreements": senate_primary_disagreements,
        "senate_not_found": senate_not_found,
        "house_0000": house_0000,
        "house_committee_missing": house_committee_missing,
    }


def _counter_to_df(
    by_congress: dict[int, collections.Counter[str]],
    name_col: str = "raw_name",
) -> pd.DataFrame:
    rows: list[dict] = []
    for c in sorted(by_congress):
        for k, v in by_congress[c].most_common():
            rows.append({name_col: k, "congress": c, "count": v})
    return pd.DataFrame(rows, columns=[name_col, "congress", "count"])


def run() -> None:
    # Fail fast if any expected Congress dir is missing. Without this, the
    # per-Congress loop below would silently emit zero-row frames for a
    # stale clone with 19th-only sparse-checkout.
    for chamber in ("sb", "hb"):
        for c in CONGRESS_SCOPE:
            d = DOCUMENT_DIR / chamber / str(c)
            if not d.is_dir() or not any(d.glob("*.toml")):
                raise RuntimeError(
                    f"Missing or empty TOML dir: {d}. "
                    f"Run `git sparse-checkout set` in the raw clone "
                    f"(paths listed in src/ingest/fetch.py)."
                )

    # Per-Congress person indexes, not a union. Measured on 100-bill
    # samples with coauthors_raw, per-Congress beat union on every
    # Congress: 17: 97.21% vs 87.36%, 18: 90.51% vs 78.83%,
    # 19: 91.57% vs 84.34%. Every disagreement was a tier-2 tie
    # introduced by non-Congress members in the candidate pool; zero
    # rescue cases.
    logger.info("loading person indexes (per-Congress)...")
    house_indexes = {
        c: build_person_indexes(PERSON_DIR, chamber="house", congress=c)
        for c in CONGRESS_SCOPE
    }
    senate_indexes = {
        c: build_person_indexes(PERSON_DIR, chamber="senate", congress=c)
        for c in CONGRESS_SCOPE
    }
    for c in CONGRESS_SCOPE:
        logger.info(
            "  %d: house=%d, senate=%d",
            c, len(house_indexes[c].in_target), len(senate_indexes[c].in_target),
        )

    logger.info("loading committee maps...")
    senate_committee_map = _load_yaml_flat(COMMITTEE_DIR / ".senate-website-key-mapping.yml")
    committee_names_by_ulid = _load_committee_names(COMMITTEE_DIR)
    house_committee_map = _load_yaml_flat(HOUSE_COMMITTEE_MAP_PATH)
    logger.info(
        "senate committee map: %d codes, %d committee TOMLs; house map: %d codes",
        len(senate_committee_map), len(committee_names_by_ulid), len(house_committee_map),
    )

    logger.info("parsing bill TOMLs (congresses: %s)...", list(CONGRESS_SCOPE))

    bills_frames: list[pd.DataFrame] = []
    authorship_frames: list[pd.DataFrame] = []
    committees_frames: list[pd.DataFrame] = []
    per_house_unresolved: dict[int, collections.Counter[str]] = {}
    per_senate_unresolved: dict[int, collections.Counter[str]] = {}
    per_house_committee_missing: dict[int, collections.Counter[str]] = {}
    senate_not_found_all: list[dict] = []
    senate_coauthor_mismatches_all: list[dict] = []
    senate_primary_disagreements_all: list[dict] = []
    house_0000_all: list[dict] = []

    for c in CONGRESS_SCOPE:
        logger.info("--- Congress %d ---", c)
        result = parse_bills(
            congress=c,
            house_indexes=house_indexes[c],
            senate_indexes=senate_indexes[c],
            senate_committee_map=senate_committee_map,
            house_committee_map=house_committee_map,
            committee_names_by_ulid=committee_names_by_ulid,
        )
        bills_frames.append(result["bills"])
        authorship_frames.append(result["authorship"])
        committees_frames.append(result["committees"])
        per_house_unresolved[c] = result["house_unresolved"]
        per_senate_unresolved[c] = result["senate_unresolved"]
        per_house_committee_missing[c] = result["house_committee_missing"]
        senate_not_found_all.extend(result["senate_not_found"])
        senate_coauthor_mismatches_all.extend(result["senate_coauthor_mismatches"])
        senate_primary_disagreements_all.extend(result["senate_primary_disagreements"])
        house_0000_all.extend(result["house_0000"])

    bills_df = pd.concat(bills_frames, ignore_index=True)
    authorship_df = pd.concat(authorship_frames, ignore_index=True)
    committees_df = pd.concat(committees_frames, ignore_index=True)

    # --- summary ---
    logger.info("=" * 60)
    logger.info("bills          : %d  (senate=%d, house=%d)",
                len(bills_df),
                int((bills_df["chamber"] == "senate").sum()),
                int((bills_df["chamber"] == "house").sum()))
    logger.info("authorship     : %d  (primary=%d, co_author=%d)",
                len(authorship_df),
                int((authorship_df["role"] == "primary").sum()),
                int((authorship_df["role"] == "co_author").sum()))
    logger.info("committees     : %d rows  (senate=%d, house=%d)",
                len(committees_df),
                int(committees_df["bill_id"].str.startswith("SBN-").sum()),
                int(committees_df["bill_id"].str.startswith("HBN-").sum()))
    logger.info("per-Congress bills:")
    for c, g in bills_df.groupby("congress"):
        logger.info("  %d: %d bills", c, len(g))
    logger.info("senate not_found excluded : %d", len(senate_not_found_all))
    logger.info("senate coauthor mismatches: %d", len(senate_coauthor_mismatches_all))
    logger.info("senate primary disagree   : %d", len(senate_primary_disagreements_all))
    logger.info("house 0000/sentinel bills : %d", len(house_0000_all))
    logger.info("per-Congress unresolved + committee-missing:")
    for c in CONGRESS_SCOPE:
        logger.info(
            "  %d: house_committee_missing=%d, house_unresolved=%d, senate_unresolved=%d",
            c,
            len(per_house_committee_missing[c]),
            len(per_house_unresolved[c]),
            len(per_senate_unresolved[c]),
        )
    logger.info("became_ra=True            : %d", int(bills_df["became_ra"].sum()))

    # --- write parquet ---
    logger.info("writing staging parquet...")
    atomic_write_parquet(bills_df,      STAGING_DIR / "bills.parquet")
    atomic_write_parquet(authorship_df, STAGING_DIR / "authorship.parquet")
    atomic_write_parquet(committees_df, STAGING_DIR / "committees.parquet")

    # --- write diagnostics ---
    logger.info("writing diagnostics...")
    atomic_write_csv(_counter_to_df(per_house_unresolved),
                     OUTPUTS_DIR / "house_unresolved_coauthors.csv")
    atomic_write_csv(_counter_to_df(per_senate_unresolved),
                     OUTPUTS_DIR / "senate_unresolved_coauthors.csv")
    atomic_write_csv(pd.DataFrame(senate_coauthor_mismatches_all),
                     OUTPUTS_DIR / "senate_coauthor_mismatches.csv")
    atomic_write_csv(pd.DataFrame(senate_primary_disagreements_all),
                     OUTPUTS_DIR / "senate_primary_disagreements.csv")
    atomic_write_csv(pd.DataFrame(senate_not_found_all),
                     OUTPUTS_DIR / "senate_not_found.csv")
    atomic_write_csv(pd.DataFrame(house_0000_all),
                     OUTPUTS_DIR / "house_0000_sentinel_bills.csv")
    atomic_write_csv(_counter_to_df(per_house_committee_missing, name_col="code"),
                     OUTPUTS_DIR / "house_committee_missing_codes.csv")

    logger.info("done.")


if __name__ == "__main__":
    run()