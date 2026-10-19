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


def _senate_coauthors_from_history(d: dict) -> list[tuple[str, int]]:
    """[(raw_name, sbn)] from ``Letter from Senator ... intention to ... SBN-N``."""
    out: list[tuple[str, int]] = []
    for h in d.get("history") or []:
        action = h.get("action") or ""
        # Fast prefilter — no false positives, keeps the walk off most actions
        if "Letter from Senator " not in action:
            continue
        if "intention to" not in action:
            continue
        if "SBN-" not in action:
            continue

        ns = action.find("Letter from Senator ") + len("Letter from Senator ")
        name, name_end = _extract_name_from(action, ns)
        if not name:
            continue

        intent_idx = action.find("intention to", name_end)
        if intent_idx == -1:
            continue
        sbn_idx = action.find("SBN-", intent_idx)
        if sbn_idx == -1:
            continue
        num_start = sbn_idx + 4
        num_end = num_start
        while num_end < len(action) and action[num_end].isdigit():
            num_end += 1
        if num_end == num_start:
            continue
        sbn = int(action[num_start:num_end])

        # "co-author" / "coauthor" / "co author" between "intention to" and "SBN-"
        between = action[intent_idx:sbn_idx].lower()
        compact = "".join(c for c in between if c.isalpha())
        if "coauthor" not in compact:
            continue

        out.append((name, sbn))
    return out


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
    sb_dir = DOCUMENT_DIR / "sb" / str(CONGRESS_SCOPE)
    for f in sorted(sb_dir.glob("*.toml")):
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        m = d.get("meta") or {}

        if m.get("not_found"):
            senate_not_found.append({"file": f.name})
            continue

        bill_number = m.get("bill_number")
        if bill_number is None:
            logger.warning("senate bill missing bill_number: %s", f.name)
            continue

        bill_id = f"SBN-{bill_number:05d}-{CONGRESS_SCOPE}"
        title = (m.get("title") or "").strip() or (m.get("long_title") or "").strip()
        if not title:
            title = f"Untitled {bill_id}"
            logger.warning("no title for %s", bill_id)

        bills.append({
            "bill_id": bill_id,
            "chamber": "senate",
            "congress": CONGRESS_SCOPE,
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
        seen_pids: set[str] = {primary_pid} if primary_pid else set()
        for raw_name, sbn_in_action in _senate_coauthors_from_history(d):
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
    hb_dir = DOCUMENT_DIR / "hb" / str(CONGRESS_SCOPE)
    for f in sorted(hb_dir.glob("*.toml")):
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        m = d.get("meta") or {}

        if m.get("not_found"):
            continue

        bill_number = m.get("bill_number")
        if bill_number is None:
            logger.warning("house bill missing bill_number: %s", f.name)
            continue

        bill_id = f"HBN-{bill_number:05d}-{CONGRESS_SCOPE}"
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
            "congress": CONGRESS_SCOPE,
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
        "bills": pd.DataFrame(bills),
        "authorship": pd.DataFrame(authorship),
        "committees": pd.DataFrame(committees),
        "senate_unresolved": senate_unresolved,
        "house_unresolved": house_unresolved,
        "senate_coauthor_mismatches": senate_coauthor_mismatches,
        "senate_primary_disagreements": senate_primary_disagreements,
        "senate_not_found": senate_not_found,
        "house_0000": house_0000,
        "house_committee_missing": house_committee_missing,
    }


def _counter_to_df(counter: collections.Counter[str], name_col: str = "raw_name") -> pd.DataFrame:
    if not counter:
        return pd.DataFrame(columns=[name_col, "count"])
    return pd.DataFrame(counter.most_common(), columns=[name_col, "count"])


def run() -> None:
    logger.info("loading person indexes...")
    house_indexes = build_person_indexes(PERSON_DIR, chamber="house", congress=CONGRESS_SCOPE)
    senate_indexes = build_person_indexes(PERSON_DIR, chamber="senate", congress=CONGRESS_SCOPE)
    logger.info(
        "targets: house=%d, senate=%d",
        len(house_indexes.in_target), len(senate_indexes.in_target),
    )

    logger.info("loading committee maps...")
    senate_committee_map = _load_yaml_flat(COMMITTEE_DIR / ".senate-website-key-mapping.yml")
    committee_names_by_ulid = _load_committee_names(COMMITTEE_DIR)
    house_committee_map = _load_yaml_flat(HOUSE_COMMITTEE_MAP_PATH)
    logger.info(
        "senate committee map: %d codes, %d committee TOMLs; house map: %d codes",
        len(senate_committee_map), len(committee_names_by_ulid), len(house_committee_map),
    )

    logger.info("parsing bill TOMLs...")
    result = parse_bills(
        house_indexes, senate_indexes,
        senate_committee_map, house_committee_map, committee_names_by_ulid,
    )

    bills_df = result["bills"]
    authorship_df = result["authorship"]
    committees_df = result["committees"]

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
    logger.info("senate not_found excluded : %d", len(result["senate_not_found"]))
    logger.info("senate coauthor mismatches: %d", len(result["senate_coauthor_mismatches"]))
    logger.info("senate primary disagree   : %d", len(result["senate_primary_disagreements"]))
    logger.info("house 0000/sentinel bills : %d", len(result["house_0000"]))
    logger.info("house committee missing   : %d distinct codes", len(result["house_committee_missing"]))
    logger.info("distinct senate unresolved: %d", len(result["senate_unresolved"]))
    logger.info("distinct house  unresolved: %d", len(result["house_unresolved"]))
    logger.info("became_ra=True            : %d", int(bills_df["became_ra"].sum()))

    # --- write parquet ---
    logger.info("writing staging parquet...")
    atomic_write_parquet(bills_df,      STAGING_DIR / "bills.parquet")
    atomic_write_parquet(authorship_df, STAGING_DIR / "authorship.parquet")
    atomic_write_parquet(committees_df, STAGING_DIR / "committees.parquet")

    # --- write diagnostics ---
    logger.info("writing diagnostics...")
    atomic_write_csv(_counter_to_df(result["house_unresolved"]),
                     OUTPUTS_DIR / "house_unresolved_coauthors.csv")
    atomic_write_csv(_counter_to_df(result["senate_unresolved"]),
                     OUTPUTS_DIR / "senate_unresolved_coauthors.csv")
    atomic_write_csv(pd.DataFrame(result["senate_coauthor_mismatches"]),
                     OUTPUTS_DIR / "senate_coauthor_mismatches.csv")
    atomic_write_csv(pd.DataFrame(result["senate_primary_disagreements"]),
                     OUTPUTS_DIR / "senate_primary_disagreements.csv")
    atomic_write_csv(pd.DataFrame(result["senate_not_found"]),
                     OUTPUTS_DIR / "senate_not_found.csv")
    atomic_write_csv(pd.DataFrame(result["house_0000"]),
                     OUTPUTS_DIR / "house_0000_sentinel_bills.csv")
    atomic_write_csv(_counter_to_df(result["house_committee_missing"], name_col="code"),
                     OUTPUTS_DIR / "house_committee_missing_codes.csv")

    logger.info("done.")


if __name__ == "__main__":
    run()