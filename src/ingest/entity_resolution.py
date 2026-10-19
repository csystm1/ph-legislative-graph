"""Entity resolution for legislator names.

Resolves raw author/co-author strings from bill metadata to person ULIDs
from the open-congress-data person TOMLs.

Public API:
    build_person_indexes(persons_dir, *, chamber, congress) -> PersonIndexes
    resolve_house_coauthor(raw_name, bill_author_codes, indexes) -> (pid, method)
    resolve_senate_coauthor(raw_name, indexes) -> (pid, method)

Verified floor on HB-19 co-author names: 91.95% (17716/19266).
Run ``python -m src.ingest.entity_resolution`` to re-verify; the
self-test asserts the ratio stays >= 0.91.
"""
from __future__ import annotations

import collections
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------- Manual overrides (hardcoded, verified in recon §6.1) ----------

MANUAL_RESOLVE = {
    # Nickname not derivable from stored name
    ("ESCUDERO", "DETTE"): ("ESCUDERO", "BERNADETTE"),
}

MANUAL_SKIP = {
    ("MASTURA", "SITTIE"),      # distinct person, not in 19H roster
    ("CRUZ", "BOY"),            # nickname, ambiguous between Ricardo & Ambrosio
    ("LEGARDA", "AA"),          # bad source encoding (Sen. Loren leaking into HB)
}

SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}


# ---------- Index bundle ----------

@dataclass(frozen=True)
class PersonIndexes:
    persons: dict[str, dict]                    # pid -> full person TOML
    senate_key_index: dict[str, str]            # senate website key -> pid
    house_author_key_index: dict[str, str]      # house author code -> pid
    in_target: frozenset[str]                   # pids in (chamber, congress)
    target_info: dict[str, dict]                # pid -> {last_tokens, first_tokens}


# ---------- Normalization ----------

def _norm_raw(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).upper()
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    s = re.sub(r'[".]', "", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- Person-side tokens ----------

def _person_first_tokens(person: dict) -> set[str]:
    tokens: set[str] = set()
    for raw in [person.get("first_name") or ""] + list(person.get("aliases") or []):
        if not raw:
            continue
        for t in raw.split():
            c = _norm_raw(t)
            if not c:
                continue
            tokens.add(c)
            if c == "MA":
                tokens.add("MARIA")
            if "-" in c:
                for p in c.split("-"):
                    if p:
                        tokens.add(p)
                tokens.add(c.replace("-", ""))
    mid = _norm_raw(person.get("middle_name") or "")
    if mid:
        tokens.add(mid)
    return tokens


def _person_last_tokens(person: dict) -> set[str]:
    last = _norm_raw(person.get("last_name") or "")
    if not last:
        return set()
    out = {last}
    if "-" in last:
        out.update(p for p in last.split("-") if p)
    return out


# ---------- Index builder ----------

def build_person_indexes(
    persons_dir: Path,
    *,
    chamber: str = "house",
    congress: int = 19,
) -> PersonIndexes:
    """Load person TOMLs and build the lookup indexes.

    ``chamber`` and ``congress`` select which persons are eligible as
    resolution targets — the matcher only considers persons with a
    ``[[memberships]]`` entry matching both. Phase 2 swaps these for
    other Congresses without touching the matcher logic.
    """
    persons: dict[str, dict] = {}
    senate_key_index: dict[str, str] = {}
    house_author_key_index: dict[str, str] = {}
    in_target: set[str] = set()

    for f in persons_dir.glob("*.toml"):
        d = tomllib.loads(f.read_text(encoding="utf-8"))
        pid = d["id"]
        persons[pid] = d
        for k in d.get("senate_website_keys") or []:
            senate_key_index[k] = pid
        for k in d.get("congress_website_author_keys") or []:
            house_author_key_index[k] = pid
        if any(
            m.get("congress") == congress and m.get("subtype") == chamber
            for m in (d.get("memberships") or [])
        ):
            in_target.add(pid)

    target_info = {
        pid: {
            "last_tokens": _person_last_tokens(persons[pid]),
            "first_tokens": _person_first_tokens(persons[pid]),
        }
        for pid in in_target
    }

    logger.info(
        "person indexes: %d persons total, %d in %s-%d, senate_keys=%d, house_codes=%d",
        len(persons), len(in_target), chamber, congress,
        len(senate_key_index), len(house_author_key_index),
    )

    return PersonIndexes(
        persons=persons,
        senate_key_index=senate_key_index,
        house_author_key_index=house_author_key_index,
        in_target=frozenset(in_target),
        target_info=target_info,
    )


# ---------- Query parsing ----------

def _parse_query(raw_name: str) -> dict | None:
    n = _norm_raw(raw_name)
    if ", " not in n:
        return None
    last_raw, rest = n.split(", ", 1)
    rtoks = rest.split()
    if len(rtoks) >= 2 and rtoks[-1] == last_raw:
        rtoks = rtoks[:-1]
    rtoks = [t for t in rtoks if t not in SUFFIXES]
    if not rtoks:
        return None

    last_variants = {last_raw}
    if "-" in last_raw:
        last_variants.update(p for p in last_raw.split("-") if p)

    ftoks: set[str] = set()
    for t in rtoks:
        if not t:
            continue
        ftoks.add(t)
        if t == "MA":
            ftoks.add("MARIA")
        if "-" in t:
            for p in t.split("-"):
                if p:
                    ftoks.add(p)
            ftoks.add(t.replace("-", ""))

    return {
        "last_raw": last_raw,
        "first_raw": rtoks[0],
        "last_variants": last_variants,
        "first_tokens": ftoks,
    }


# ---------- Edit distance ----------

def _ed(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------- Candidate tier ----------

def _candidate_tier(q_first: set[str], info: dict) -> int | None:
    s_first = info["first_tokens"]

    # T2: exact token
    if q_first & s_first:
        return 2

    # T4: abbreviation (prefix, both >= 3 chars)
    for qt in q_first:
        if len(qt) < 3:
            continue
        for st in s_first:
            if len(st) < 3:
                continue
            if st.startswith(qt) or qt.startswith(st):
                return 4

    # T5: edit distance 1, longest token >= 4
    for qt in q_first:
        if len(qt) < 3:
            continue
        for st in s_first:
            if len(st) < 3:
                continue
            if max(len(qt), len(st)) >= 4 and _ed(qt, st) <= 1:
                return 5

    # T6: edit distance 2, both >= 6
    for qt in q_first:
        if len(qt) < 6:
            continue
        for st in s_first:
            if len(st) < 6:
                continue
            if _ed(qt, st) <= 2:
                return 6

    # T8: composite initials — 2 distinct letters, each = initial of a stored token
    initials_avail = {st[0] for st in s_first if st}
    for qt in q_first:
        if len(qt) == 2 and len(set(qt)) == 2:
            if qt[0] in initials_avail and qt[1] in initials_avail:
                return 8

    return None


# ---------- Core resolution ----------

def _resolve_by_signature(
    raw_name: str,
    bill_author_codes: list[str],
    indexes: PersonIndexes,
) -> tuple[str | None, str]:
    """Resolve a ``"LAST, FIRST ..."`` name against the target persons.

    Returns ``(person_id, method)``. ``method`` is one of:
    manual_skip, manual_override, manual_override_missing, unparseable,
    no_candidate, bill_match, tier_{2,4,5,6,8}, ambiguous_tier_{N}.
    """
    parsed = _parse_query(raw_name)
    if not parsed:
        return (None, "unparseable")

    sig = (parsed["last_raw"], parsed["first_raw"])
    if sig in MANUAL_SKIP:
        return (None, "manual_skip")
    if sig in MANUAL_RESOLVE:
        tgt_last, tgt_first = MANUAL_RESOLVE[sig]
        for pid in indexes.in_target:
            info = indexes.target_info[pid]
            if tgt_last in info["last_tokens"] and tgt_first in info["first_tokens"]:
                return (pid, "manual_override")
        return (None, "manual_override_missing")

    candidates: dict[str, int] = {}
    for pid, info in indexes.target_info.items():
        if not (parsed["last_variants"] & info["last_tokens"]):
            continue
        tier = _candidate_tier(parsed["first_tokens"], info)
        if tier is not None:
            candidates[pid] = tier

    if not candidates:
        return (None, "no_candidate")

    # Bill tiebreaker: exactly one candidate is on this bill's author codes
    bill_pids = {
        indexes.house_author_key_index[c]
        for c in bill_author_codes
        if c in indexes.house_author_key_index
    }
    on_bill = set(candidates) & bill_pids
    if len(on_bill) == 1:
        return (next(iter(on_bill)), "bill_match")

    best = min(candidates.values())
    at_best = [pid for pid, t in candidates.items() if t == best]
    if len(at_best) == 1:
        return (at_best[0], f"tier_{best}")

    return (None, f"ambiguous_tier_{best}")


# ---------- Public resolvers ----------

def resolve_house_coauthor(
    raw_name: str,
    bill_author_codes: list[str],
    indexes: PersonIndexes,
) -> tuple[str | None, str]:
    """Resolve a House co-author name (``"LAST, FIRST M."``).

    ``bill_author_codes`` is the bill's full author-code list (from
    ``meta.congress_website_author_codes``), used only as a tiebreaker
    when the name is ambiguous.
    """
    return _resolve_by_signature(raw_name, bill_author_codes, indexes)


def resolve_senate_coauthor(
    raw_name: str,
    indexes: PersonIndexes,
) -> tuple[str | None, str]:
    """Resolve a Senate co-author name from a ``[[history]]`` action.

    Senate history prose uses ``"FIRST MIDDLE LAST"`` (no comma). Rewrite
    to ``"LAST, FIRST MIDDLE"`` before delegating. No bill-code
    tiebreaker on this side — Senate co-authors are parsed from prose,
    not from codes.
    """
    name = raw_name.strip()
    if "," not in name:
        parts = name.split()
        if len(parts) >= 2:
            name = f"{parts[-1]}, {' '.join(parts[:-1])}"
    return _resolve_by_signature(name, [], indexes)


# ---------- Self-test ----------

if __name__ == "__main__":
    import time

    from src.utils.config import CONGRESS_SCOPE, DOCUMENT_DIR, PERSON_DIR

    # Per-Congress resolution floors. 19 preserves the original floor;
    # 17/18 use conservative floors pending population-level re-measurement.
    FLOORS = {17: 0.85, 18: 0.85, 19: 0.91}
    DEFAULT_FLOOR = 0.85

    for congress in CONGRESS_SCOPE:
        t0 = time.monotonic()
        indexes = build_person_indexes(PERSON_DIR, chamber="house", congress=congress)
        logger.info("indexes built in %.2fs", time.monotonic() - t0)

        stats: collections.Counter[str] = collections.Counter()
        unresolved: collections.Counter[str] = collections.Counter()
        resolved_pids: set[str] = set()

        hb_dir = DOCUMENT_DIR / "hb" / str(congress)
        for f in hb_dir.glob("*.toml"):
            d = tomllib.loads(f.read_text(encoding="utf-8"))
            m = d["meta"]
            codes = list(m.get("congress_website_author_codes") or [])
            for raw in m.get("congress_website_coauthors_raw") or []:
                pid, method = resolve_house_coauthor(raw, codes, indexes)
                stats["_total"] += 1
                stats[method] += 1
                if pid:
                    resolved_pids.add(pid)
                    stats["_resolved"] += 1
                else:
                    unresolved[raw] += 1

        total = stats["_total"]
        resolved = stats["_resolved"]
        ratio = resolved / total if total else 0.0
        floor = FLOORS.get(congress, DEFAULT_FLOOR)

        logger.info("=== HB-%d co-author resolution ===", congress)
        logger.info("total raw names: %d", total)
        logger.info("resolved       : %d/%d = %.2f%%", resolved, total, ratio * 100)
        for k in sorted(stats):
            if k.startswith("_"):
                continue
            logger.info("  %-30s %6d  (%.2f%%)", k, stats[k], stats[k] / total * 100)
        logger.info("distinct unresolved: %d", len(unresolved))

        assert total > 0, f"no HB-{congress} co-author rows parsed — did the sparse checkout run?"
        assert ratio >= floor, (
            f"HB-{congress} resolution {ratio:.4f} fell below floor {floor} — matcher regression"
        )

    logger.info("self-test OK")