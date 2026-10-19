# Philippine Legislative Bills Graph

A co-authorship and committee-funnel analysis of Philippine Senate and
House bills, built from the [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data)
corpus. Who files bills with whom, which committees bury them, and how
legislative coalitions shift across Congresses.

**Status:** Phase 2 (17th–19th Congress, ingest to staging).
**Data current as of:** 2025-10-16 (last upstream commit; 20th Congress excluded as incomplete).

## What this project does

Three analyses over the metadata of filed bills:

1. **Co-authorship network** — who files with whom, community structure, bipartisanship rate.
2. **Committee funnel** — bills per committee, exit rate, which committees are graveyards.
3. **Temporal evolution** — coalition shifts across the 17th, 18th, and 19th Congresses.

The interesting unit of analysis is *bills*, not votes — the point is which
legislative relationships the filing system actually encodes.

## Data

Source: `open-congress-data`, a community-maintained TOML-encoded corpus of
Philippine legislative documents (8th–20th Congress). We use the 17th–19th
Congresses (2016–2025); the 20th is excluded as incomplete upstream.

| Congress | Senate | House | Total |
|---|---:|---:|---:|
| 17th (2016–2019) | 2,235 | 9,215 | 11,450 |
| 18th (2019–2022) | 2,524 | 10,853 | 13,377 |
| 19th (2022–2025) | 3,002 | 11,561 | 14,563 |
| **Total** | **7,761** | **31,629** | **39,390** |

- **Authorship edges:** 108,838 (39,357 primary, 69,481 co-authors)
- **Committee assignments:** 47,940 rows
- **Persons:** 1,173 legislators across 13 Congresses; 302/307/317 with
  17th/18th/19th House membership

Data integrity rules live in `docs/data_contract.yaml`; per-run diagnostics land in `outputs/` (gitignored, generated per-run).

## Limitations

- **20th Congress excluded** — incomplete upstream; Phase 2 is 17th–19th Congress.
- **House co-author resolution** — per-Congress: 17th 97.33%, 18th 92.74%,
  19th 91.95%. Distinct unresolved: 10/19/28. Logged per-Congress to
  `outputs/house_unresolved_coauthors.csv`. Senate unresolved: 9/5/8 distinct
  in `outputs/senate_unresolved_coauthors.csv`. Floors: do not iterate.
- **Unmapped House committee codes** — 9 distinct across the three Congresses
  (`C503`, `B510`, `A507`, `J506`, `K503`, `K504`, `K505`, +2 unseen in the
  5-row sample); 41 rows fall back to code-as-name. `outputs/house_committee_missing_codes.csv`
  is Congress-tagged. Extending the mapping is deferred (needs a browser
  session with Cloudflare clearance — see `src/ingest/fetch.py`).
- **Sentinel bills** — 33 bills lack a primary authorship row; 20 lack any
  authorship. Same data-gap class across all three Congresses; `0000` /
  empty author codes upstream. Validation warns, not fails.
- **Untitled bills** — 10 bills lack all title fields (9 in the 18th, 1 in
  the 19th); fallback `"Untitled <bill_id>"` is used.
- **Upstream manual-encoding disclaimer** — `open-congress-data` is community-maintained
  and self-describes a manual-encoding process; parsed data inherits its
  accuracy characteristics.
- **Data snapshot** — upstream HEAD `6e853ee`, 2025-10-16. Re-run
  `python -m src.ingest.fetch` to refresh.

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