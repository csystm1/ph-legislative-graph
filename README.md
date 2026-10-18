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

Data completeness is documented in `docs/data_contract.yaml` (later phase) and
`outputs/` (generated per-run).

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
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

make fetch    # sparse-clone open-congress-data into data/raw/
make parse    # TOML → data/staging/*.parquet
make validate # validation gate
make load     # Postgres load (idempotent)
make analyze  # graph build, communities, funnel
make site     # assemble docs/ for GitHub Pages
```

(Entries to be filled in as each stage lands.)

## Reference

- Predecessor: [ph-republic-acts-de](https://github.com/csystm/ph-republic-acts-de) — TF-IDF + K-Means over Republic Acts.
- Upstream data: [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data).