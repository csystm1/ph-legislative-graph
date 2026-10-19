"""Pre-load integrity checks over the three staging parquets.

Contract constants (expected columns, required-non-null, thresholds,
patterns) are read from docs/data_contract.yaml via src.validation.contract.
Editing the YAML changes pipeline behavior; there is no second source of truth.

Each check returns a CheckResult with status "pass" | "warn" | "fail".
Errors gate the pipeline (exit code 1); warnings are reported, not gated.

Usage:  python -m src.validation.checks
Output: outputs/validation_report.json
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src.utils.config import CONGRESS_SCOPE, OUTPUTS_DIR, STAGING_DIR
from src.utils.io import atomic_write_json
from src.utils.logger import get_logger
from src.validation.contract import Contract, TableContract

try:  # io._load_yaml_flat lives with the other I/O helpers
    from src.utils.io import _load_yaml_flat
except ImportError:  # fallback: same shape, defined here
    import yaml
    def _load_yaml_flat(path: Path) -> dict[str, str]:
        with Path(path).open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

logger = get_logger(__name__)

_CONTRACT = Contract()

# Fail fast on contract/config drift.
assert _CONTRACT.congress_scope == CONGRESS_SCOPE, (
    f"contract congress_scope={_CONTRACT.congress_scope} != "
    f"config CONGRESS_SCOPE={CONGRESS_SCOPE}"
)

# Contract-derived constants — no hardcoded schema facts below this line.
_BILLS      = _CONTRACT.table("bills")
_AUTHORSHIP = _CONTRACT.table("authorship")
_COMMITTEES = _CONTRACT.table("committees")

_CHAMBER_ALLOWED  = _BILLS.rule_params("bills_chamber_domain")["allowed"]
_ROLE_ALLOWED     = _AUTHORSHIP.rule_params("authorship_role_domain")["allowed"]
_PREFIX_MAP       = _BILLS.rule_params("bills_id_prefix_chamber")["prefix_map"]
_MIN_FILED_DATE   = pd.Timestamp(_BILLS.rule_params("bills_filed_date_valid")["min_date"])
_TODAY            = pd.Timestamp.today().normalize()

_FALLBACK_MAP_PATH    = Path(_COMMITTEES.rule_params("committees_code_fallback_count")["mapping_path"])
_FALLBACK_MAX         = int(_COMMITTEES.rule_params("committees_code_fallback_count")["max_expected"])
_NO_PRIMARY_MAX       = int(_CONTRACT.cross_rule_params("bills_have_primary_author")["max_expected_without_primary"])
_ANY_AUTHORSHIP_MAX   = int(_CONTRACT.cross_rule_params("bills_have_any_authorship")["max_expected"])

SAMPLE_N = 5


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #

@dataclass
class CheckResult:
    name: str
    severity: str          # "error" | "warn"
    status: str            # "pass" | "fail" | "warn"
    message: str
    violations: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class Staging:
    bills: pd.DataFrame
    authorship: pd.DataFrame
    committees: pd.DataFrame


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _normalize_dtype(d: Any) -> str:
    s = str(d)
    return {"Int64": "int64", "boolean": "bool"}.get(s, s)


def _check_table_schema(df: pd.DataFrame, tc: TableContract, name: str) -> CheckResult:
    expected = tc.expected_columns
    missing = [c for c in expected if c not in df.columns]
    extra = [c for c in df.columns if c not in expected]
    wrong_dtype = [
        f"{c}: expected {expected[c]}, got {_normalize_dtype(df[c].dtype)}"
        for c in expected
        if c in df.columns
        and _normalize_dtype(df[c].dtype) != _normalize_dtype(expected[c])
    ]
    problems = (
        [f"missing column: {c}" for c in missing]
        + [f"unexpected column: {c}" for c in extra]
        + wrong_dtype
    )
    return CheckResult(
        name=name,
        severity="error",
        status="pass" if not problems else "fail",
        message=("Schema conforms" if not problems
                 else f"{len(problems)} schema problem(s)"),
        violations=len(problems),
        samples=problems[:SAMPLE_N],
    )


def _pass(name: str, msg: str) -> CheckResult:
    return CheckResult(name=name, severity="error", status="pass", message=msg)


def _fail(name: str, msg: str, n: int, samples: list[str] | None = None) -> CheckResult:
    return CheckResult(name=name, severity="error", status="fail",
                       message=msg, violations=n, samples=samples or [])


def _warn(name: str, msg: str, n: int = 0, samples: list[str] | None = None) -> CheckResult:
    return CheckResult(name=name, severity="warn",
                       status="warn" if n or msg else "pass",
                       message=msg, violations=n, samples=samples or [])


# --------------------------------------------------------------------------- #
# bills checks
# --------------------------------------------------------------------------- #

def check_bills_schema(s: Staging) -> CheckResult:
    return _check_table_schema(s.bills, _BILLS, "bills_schema")


def check_bills_bill_id_unique(s: Staging) -> CheckResult:
    df = s.bills
    dup = df["bill_id"].duplicated(keep=False)
    n = int(dup.sum())
    if n == 0:
        return _pass("bills_bill_id_unique", "bill_id is unique")
    return _fail("bills_bill_id_unique", f"{n} duplicate bill_id row(s)", n,
                 df.loc[dup, "bill_id"].drop_duplicates().head(SAMPLE_N).tolist())


def check_bills_chamber_domain(s: Staging) -> CheckResult:
    df = s.bills
    bad = df[~df["chamber"].isin(_CHAMBER_ALLOWED)]
    n = len(bad)
    if n == 0:
        return _pass("bills_chamber_domain",
                     f"chamber ∈ {_CHAMBER_ALLOWED}")
    return _fail("bills_chamber_domain",
                 f"{n} bill(s) outside {_CHAMBER_ALLOWED}", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


def check_bills_congress_scope(s: Staging) -> CheckResult:
    df = s.bills
    bad = df[df["congress"] != CONGRESS_SCOPE]
    n = len(bad)
    if n == 0:
        return _pass("bills_congress_scope",
                     f"All bills in congress {CONGRESS_SCOPE}")
    return _fail("bills_congress_scope",
                 f"{n} bill(s) not in congress {CONGRESS_SCOPE}", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


def check_bills_id_prefix_chamber(s: Staging) -> CheckResult:
    df = s.bills
    bad_idx = [
        i for i, (bid, chamber) in enumerate(zip(df["bill_id"], df["chamber"]))
        if not str(bid).startswith(_PREFIX_MAP.get(chamber, "\0"))
    ]
    n = len(bad_idx)
    if n == 0:
        return _pass("bills_id_prefix_chamber",
                     "bill_id prefix matches chamber for all rows")
    bad = df.iloc[bad_idx]
    samples = [f"{r.bill_id} ({r.chamber})" for r in bad.head(SAMPLE_N).itertuples()]
    return _fail("bills_id_prefix_chamber",
                 f"{n} bill_id(s) prefix does not match chamber", n, samples)


def check_bills_filed_date_valid(s: Staging) -> CheckResult:
    df = s.bills
    raw = df["filed_date"]
    parsed = pd.to_datetime(raw, errors="coerce")
    non_null = raw.notna()
    unparseable = non_null & parsed.isna()
    out_of_range = parsed.notna() & (
        (parsed < _MIN_FILED_DATE) | (parsed > _TODAY)
    )
    bad_mask = unparseable | out_of_range
    n = int(bad_mask.sum())
    if n == 0:
        return _pass("bills_filed_date_valid",
                     f"All non-null filed_date values parse and fall in "
                     f"[{_MIN_FILED_DATE.date()}, {_TODAY.date()}]")
    samples = [
        f"{bid}: {raw_val!r}"
        for bid, raw_val in df.loc[bad_mask, ["bill_id", "filed_date"]]
            .head(SAMPLE_N).itertuples(index=False)
    ]
    return _fail("bills_filed_date_valid",
                 f"{n} filed_date value(s) unparseable or out of range", n, samples)


def check_bills_became_ra_status_consistency(s: Staging) -> CheckResult:
    df = s.bills
    mask = df["became_ra"].astype(bool) & df["status"].isna()
    n = int(mask.sum())
    if n == 0:
        return _pass("bills_became_ra_status_consistency",
                     "All became_ra=True rows carry a status")
    return _warn("bills_became_ra_status_consistency",
                 f"{n} became_ra=True bill(s) have null status", n,
                 df.loc[mask, "bill_id"].head(SAMPLE_N).tolist())


# --------------------------------------------------------------------------- #
# authorship checks
# --------------------------------------------------------------------------- #

def check_authorship_schema(s: Staging) -> CheckResult:
    return _check_table_schema(s.authorship, _AUTHORSHIP, "authorship_schema")


def check_authorship_role_domain(s: Staging) -> CheckResult:
    df = s.authorship
    bad = df[~df["role"].isin(_ROLE_ALLOWED)]
    n = len(bad)
    if n == 0:
        return _pass("authorship_role_domain", f"role ∈ {_ROLE_ALLOWED}")
    return _fail("authorship_role_domain",
                 f"{n} row(s) outside role domain {_ROLE_ALLOWED}", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


def check_authorship_pk_unique(s: Staging) -> CheckResult:
    df = s.authorship
    key_cols = ["bill_id", "legislator_id", "role"]
    dup = df.duplicated(subset=key_cols, keep=False)
    n = int(dup.sum())
    if n == 0:
        return _pass("authorship_pk_unique",
                     f"({', '.join(key_cols)}) is unique")
    samples = (
        df.loc[dup, key_cols]
          .drop_duplicates()
          .head(SAMPLE_N)
          .astype(str).agg("/".join, axis=1).tolist()
    )
    return _fail("authorship_pk_unique",
                 f"{n} row(s) share a PK tuple", n, samples)


def check_authorship_bill_id_fk(s: Staging) -> CheckResult:
    df, valid = s.authorship, set(s.bills["bill_id"])
    orphans = df[~df["bill_id"].isin(valid)]
    n = len(orphans)
    if n == 0:
        return _pass("authorship_bill_id_fk",
                     "authorship.bill_id ⊆ bills.bill_id")
    return _fail("authorship_bill_id_fk",
                 f"{n} authorship row(s) reference unknown bill_id", n,
                 orphans["bill_id"].drop_duplicates().head(SAMPLE_N).tolist())


def check_authorship_co_author_resolution_method_non_null(s: Staging) -> CheckResult:
    df = s.authorship
    coa = df[df["role"] == "co_author"]
    bad = coa[coa["resolution_method"].isna()]
    n = len(bad)
    if n == 0:
        return _pass("authorship_co_author_resolution_method_non_null",
                     f"All {len(coa)} co_author rows carry a resolution_method")
    return _fail("authorship_co_author_resolution_method_non_null",
                 f"{n}/{len(coa)} co_author row(s) have null resolution_method", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


def check_authorship_primary_resolution_method_present(s: Staging) -> CheckResult:
    df = s.authorship
    prim = df[df["role"] == "primary"]
    bad = prim[prim["resolution_method"].isna()]
    n = len(bad)
    if n == 0:
        return _pass("authorship_primary_resolution_method_present",
                     f"All {len(prim)} primary rows carry a resolution_method")
    return _warn("authorship_primary_resolution_method_present",
                 f"{n}/{len(prim)} primary row(s) have null resolution_method", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


# --------------------------------------------------------------------------- #
# committees checks
# --------------------------------------------------------------------------- #

def check_committees_schema(s: Staging) -> CheckResult:
    return _check_table_schema(s.committees, _COMMITTEES, "committees_schema")


def check_committees_pk_unique(s: Staging) -> CheckResult:
    df = s.committees
    key_cols = ["bill_id", "committee_name"]
    dup = df.duplicated(subset=key_cols, keep=False)
    n = int(dup.sum())
    if n == 0:
        return _pass("committees_pk_unique",
                     f"({', '.join(key_cols)}) is unique")
    samples = (
        df.loc[dup, key_cols].drop_duplicates().head(SAMPLE_N)
          .astype(str).agg("/".join, axis=1).tolist()
    )
    return _fail("committees_pk_unique",
                 f"{n} row(s) share a PK tuple", n, samples)


def check_committees_bill_id_fk(s: Staging) -> CheckResult:
    df, valid = s.committees, set(s.bills["bill_id"])
    orphans = df[~df["bill_id"].isin(valid)]
    n = len(orphans)
    if n == 0:
        return _pass("committees_bill_id_fk",
                     "committees.bill_id ⊆ bills.bill_id")
    return _fail("committees_bill_id_fk",
                 f"{n} committee row(s) reference unknown bill_id", n,
                 orphans["bill_id"].drop_duplicates().head(SAMPLE_N).tolist())


def check_committees_name_non_empty(s: Staging) -> CheckResult:
    df = s.committees
    s_ = df["committee_name"]
    bad = df[s_.isna() | (s_.fillna("").str.strip() == "")]
    n = len(bad)
    if n == 0:
        return _pass("committees_name_non_empty",
                     "All committee_name values non-null and non-empty")
    return _warn("committees_name_non_empty",
                 f"{n} committee row(s) have null/empty name", n,
                 bad["bill_id"].head(SAMPLE_N).tolist())


_CODE_SHAPE = r"[A-Z]\d{3}|\d{4}"

def check_committees_code_fallback_count(s: Staging) -> CheckResult:
    mapping = _load_yaml_flat(_FALLBACK_MAP_PATH)
    mapped_values = set(mapping.values())
    df = s.committees
    names = df["committee_name"].fillna("")
    looks_like_code = names.str.fullmatch(_CODE_SHAPE)
    fallback = df[looks_like_code & ~names.isin(mapped_values)]
    n = len(fallback)
    if n <= _FALLBACK_MAX:
        return _pass("committees_code_fallback_count",
                     f"{n} unmapped-code fallback row(s) "
                     f"(threshold {_FALLBACK_MAX})")
    return _warn("committees_code_fallback_count",
                 f"{n} unmapped-code fallback row(s) exceeds threshold "
                 f"{_FALLBACK_MAX}", n,
                 fallback["committee_name"].drop_duplicates().head(SAMPLE_N).tolist())


# --------------------------------------------------------------------------- #
# cross-table checks
# --------------------------------------------------------------------------- #

def check_bills_have_primary_author(s: Staging) -> CheckResult:
    prim_ids = set(s.authorship.loc[s.authorship["role"] == "primary", "bill_id"])
    missing = s.bills.loc[~s.bills["bill_id"].isin(prim_ids), "bill_id"]
    n = len(missing)
    if n <= _NO_PRIMARY_MAX:
        return _pass("bills_have_primary_author",
                     f"{n} bill(s) without a primary author "
                     f"(threshold {_NO_PRIMARY_MAX}; sentinels expected)")
    return _warn("bills_have_primary_author",
                 f"{n} bill(s) without a primary author exceeds threshold "
                 f"{_NO_PRIMARY_MAX}", n, missing.head(SAMPLE_N).tolist())


def check_bills_have_any_authorship(s: Staging) -> CheckResult:
    have = set(s.authorship["bill_id"])
    missing = s.bills.loc[~s.bills["bill_id"].isin(have), "bill_id"]
    n = len(missing)
    if n <= _ANY_AUTHORSHIP_MAX:
        return CheckResult(
            name="bills_have_any_authorship",
            severity="warn",
            status="pass",
            message=f"{n} bill(s) with no authorship rows "
                    f"(threshold {_ANY_AUTHORSHIP_MAX}; sentinels expected)",
        )
    return _warn("bills_have_any_authorship",
                 f"{n} bill(s) with no authorship rows exceeds threshold "
                 f"{_ANY_AUTHORSHIP_MAX}", n, missing.head(SAMPLE_N).tolist())


# --------------------------------------------------------------------------- #
# Registry — must equal contract.all_rules names (drift guard below)
# --------------------------------------------------------------------------- #

CHECKS: list[Callable[[Staging], CheckResult]] = [
    check_bills_schema,
    check_bills_bill_id_unique,
    check_bills_chamber_domain,
    check_bills_congress_scope,
    check_bills_id_prefix_chamber,
    check_bills_filed_date_valid,
    check_bills_became_ra_status_consistency,
    check_authorship_schema,
    check_authorship_role_domain,
    check_authorship_pk_unique,
    check_authorship_bill_id_fk,
    check_authorship_co_author_resolution_method_non_null,
    check_authorship_primary_resolution_method_present,
    check_committees_schema,
    check_committees_pk_unique,
    check_committees_bill_id_fk,
    check_committees_name_non_empty,
    check_committees_code_fallback_count,
    check_bills_have_primary_author,
    check_bills_have_any_authorship,
]

_CODE_RULE_NAMES = {fn.__name__[len("check_"):] for fn in CHECKS}
_CONTRACT_RULE_NAMES = set(_CONTRACT.all_rules)
if _CODE_RULE_NAMES != _CONTRACT_RULE_NAMES:
    _missing_in_code = sorted(_CONTRACT_RULE_NAMES - _CODE_RULE_NAMES)
    _missing_in_yaml = sorted(_CODE_RULE_NAMES - _CONTRACT_RULE_NAMES)
    raise RuntimeError(
        "Rule registry drift between checks.py and data_contract.yaml — "
        f"in YAML not in code: {_missing_in_code}; "
        f"in code not in YAML: {_missing_in_yaml}"
    )


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def run_all(staging: Staging) -> list[CheckResult]:
    results: list[CheckResult] = []
    for fn in CHECKS:
        try:
            r = fn(staging)
        except Exception as e:
            r = CheckResult(
                name=fn.__name__[len("check_"):],
                severity="error",
                status="fail",
                message=f"check crashed: {type(e).__name__}: {e}",
            )
        results.append(r)
        level = {"pass": logger.info,
                 "warn": logger.warning,
                 "fail": logger.error}[r.status]
        level("[%s] %s — %s", r.status.upper(), r.name, r.message)
        for sample in r.samples:
            level("    %s", sample)
    return results


def summarize(results: list[CheckResult]) -> dict[str, Any]:
    errors = [r for r in results if r.status == "fail"]
    warns  = [r for r in results if r.status == "warn"]
    return {
        "contract_version": _CONTRACT.version,
        "dataset": _CONTRACT.dataset,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_checks": len(results),
        "passed": sum(1 for r in results if r.status == "pass"),
        "warned": len(warns),
        "failed": len(errors),
        "overall_status": "fail" if errors else ("warn" if warns else "pass"),
        "checks": [asdict(r) for r in results],
    }


def _load_staging() -> Staging:
    paths = {name: STAGING_DIR / f"{name}.parquet"
             for name in ("bills", "authorship", "committees")}
    frames: dict[str, pd.DataFrame] = {}
    for name, path in paths.items():
        logger.info("Loading %s", path)
        frames[name] = pd.read_parquet(path)
        logger.info("  %d rows x %d cols", *frames[name].shape)
    return Staging(**frames)


def main() -> int:
    staging = _load_staging()
    results = run_all(staging)
    report = summarize(results)

    dest = OUTPUTS_DIR / "validation_report.json"
    atomic_write_json(report, dest)
    logger.info(
        "Validation: %d passed, %d warned, %d failed — report at %s",
        report["passed"], report["warned"], report["failed"], dest,
    )
    return 0 if report["overall_status"] != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())