# Philippine Legislative Bills Graph

A co-authorship and committee-funnel analysis of Philippine Senate and
House bills, built from the [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data)
corpus. Who files bills with whom, which committees bury them, and how
legislative coalitions shift across Congresses.

**Status:** Phase 1 (19th Congress, ingest to staging).
**Data current as of:** 2025-10-16 (last upstream commit; 20th Congress excluded as incomplete).

## What this project does

Three analyses over the metadata of filed bills:

1. **Co-authorship network** — who files with whom, community structure, bipartisanship rate.
2. **Committee funnel** — bills per committee, exit rate, which committees are graveyards.
3. **Temporal evolution** — coalition shifts across Congresses (17th, 18th, 19th in Phase 2).

The interesting unit of analysis is *bills*, not votes — the point is which
legislative relationships the filing system actually encodes.

## Data

Source: `open-congress-data`, a community-maintained TOML-encoded corpus of
Philippine legislative documents (8th–20th Congress). We use the 19th Congress
(2022–2025) — the most recent *complete* Congress in the dataset.

- **Senate:** 3,007 bills (5 excluded as `not_found`)
- **House:** 11,561 bills
- **Persons:** 1,173 legislators across 13 Congresses, 317 with 19th-House membership

Data integrity rules live in `docs/data_contract.yaml`; per-run diagnostics land in `outputs/` (gitignored, generated per-run).

## Limitations

- **20th Congress excluded** — incomplete upstream; Phase 1 is 19th Congress only.
- **House co-author resolution floor: 91.95%** — 28 distinct unresolved names logged to `outputs/house_unresolved_coauthors.csv`; 8 Senate names in `outputs/senate_unresolved_coauthors.csv`.
- **4 unmapped House committee codes** (`J506`, `K503`, `K504`, `K505`) — 16 bills fall back to code-as-name; `outputs/house_committee_missing_codes.csv`.
- **Upstream manual-encoding disclaimer** — `open-congress-data` is community-maintained and self-describes a manual-encoding process; parsed data inherits its accuracy characteristics.
- **Data snapshot** — upstream HEAD `6e853ee`, 2025-10-16. Re-run `python -m src.ingest.fetch` to refresh.

## Pipeline

```
fetch (sparse clone) → parse_toml → staging parquet
                     → validate → curated parquet
                     → load → Postgres
                     → graph build → communities, funnel
                     → build_site → docs/ (GitHub Pages)
```

## Reproducing

```bash
python -m src.ingest.fetch       # sparse-clone open-congress-data into data/raw/
python -m src.ingest.parse_toml  # TOML → data/staging/*.parquet
python -m src.validation.checks  # pre-load gate; exit 0 = pass
# python -m src.load.load_postgres     # Phase 3
# python -m src.analytics.build_graph  # Phase 4
# python -m src.analytics.build_site   # Phase 5
```

Later phases are commented out until they land.

## Reference

- Predecessor: [ph-republic-acts-de](https://github.com/csystm/ph-republic-acts-de) — TF-IDF + K-Means over Republic Acts.
- Upstream data: [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data).