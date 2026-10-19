"""Load the YAML data contract and expose typed accessors.

The contract at docs/data_contract.yaml is the single source of truth for
the shape and validation rules of the staging layer. ``checks.py`` imports
its constants from here, so the code and the contract cannot drift.

Multi-table: top-level metadata (version, dataset, congress_scope) plus a
``tables:`` mapping. Use ``Contract.table(name)`` for a per-table view.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONTRACT_PATH = Path("docs") / "data_contract.yaml"

# Contract (type, format) -> pandas dtype string. Normalization of parquet
# round-trip flips (int64<->Int64, bool<->boolean) is handled in checks.py.
_PANDAS_TYPE_MAP: dict[tuple[str, str | None], str] = {
    ("string", None): "object",
    ("string", "date"): "object",
    ("integer", None): "Int64",
    ("boolean", None): "bool",
}


@lru_cache(maxsize=4)
def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _fields_to_columns(fields: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in fields:
        key = (f["type"], f.get("format"))
        if key not in _PANDAS_TYPE_MAP:
            raise KeyError(
                f"Contract field {f['name']!r} has type {key!r}, "
                f"which is not in _PANDAS_TYPE_MAP"
            )
        out[f["name"]] = _PANDAS_TYPE_MAP[key]
    return out


class TableContract:
    """Typed view over one ``tables.<name>`` section."""

    def __init__(self, data: dict[str, Any], name: str) -> None:
        self._data = data
        self.name = name

    @property
    def source(self) -> str:
        return self._data["source"]

    @property
    def primary_key(self) -> str | list[str]:
        return self._data["primary_key"]

    @property
    def schema_fields(self) -> list[dict[str, Any]]:
        return self._data["fields"]

    @property
    def expected_columns(self) -> dict[str, str]:
        return _fields_to_columns(self.schema_fields)

    @property
    def required_non_null(self) -> list[str]:
        return [f["name"] for f in self.schema_fields if not f["nullable"]]

    def field(self, name: str) -> dict[str, Any]:
        for f in self.schema_fields:
            if f["name"] == name:
                return f
        raise KeyError(f"Unknown field in {self.name}: {name}")

    @property
    def rules(self) -> dict[str, dict[str, Any]]:
        return {r["name"]: r for r in self._data.get("validation_rules", [])}

    def rule_params(self, rule_name: str) -> dict[str, Any]:
        rule = self.rules.get(rule_name)
        if rule is None:
            raise KeyError(f"No such rule in {self.name}: {rule_name}")
        return rule.get("params", {}) or {}


class Contract:
    """Typed view over docs/data_contract.yaml."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else _DEFAULT_CONTRACT_PATH
        self._data = _read_yaml(self.path)
        self._tables: dict[str, TableContract] = {
            name: TableContract(section, name)
            for name, section in self._data["tables"].items()
        }

    @property
    def version(self) -> str:
        return self._data["contract_version"]

    @property
    def dataset(self) -> str:
        return self._data["dataset"]

    @property
    def congress_scope(self) -> tuple[int, ...]:
        raw = self._data["congress_scope"]
        if not isinstance(raw, (list, tuple)) or not raw:
            raise TypeError(
                f"contract congress_scope must be a non-empty list of ints "
                f"(got {type(raw).__name__}: {raw!r}). "
                f"Contract v2.0 is multi-Congress; revert the YAML or update this accessor."
            )
        return tuple(int(x) for x in raw)

    @property
    def tables(self) -> list[str]:
        return list(self._tables)

    def table(self, name: str) -> TableContract:
        if name not in self._tables:
            raise KeyError(f"No such table in contract: {name}")
        return self._tables[name]

    @property
    def all_rules(self) -> dict[str, dict[str, Any]]:
        """Flat name -> rule dict across all tables + cross-table section.

        Rule names must be globally unique; drift guard in checks.py
        asserts the set equals the registered check functions.
        """
        out: dict[str, dict[str, Any]] = {}
        for tc in self._tables.values():
            out.update(tc.rules)
        for r in self._data.get("cross_table_validation_rules", []):
            out[r["name"]] = r
        return out

    def cross_rule_params(self, rule_name: str) -> dict[str, Any]:
        rule = self.all_rules.get(rule_name)
        if rule is None:
            raise KeyError(f"No such rule in contract: {rule_name}")
        return rule.get("params", {}) or {}