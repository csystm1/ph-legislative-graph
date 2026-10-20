# Philippine Legislative Bills Graph

A co-authorship and committee-funnel analysis of Philippine Senate and
House bills, built from the [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data)
corpus. Who files bills with whom, which committees bury them, and how
legislative coalitions shift across Congresses.

**Status:** Phase 5 (17th–19th Congress, graph + analytics + site).
**Live site:** [csystm1.github.io/ph-legislative-graph](https://csystm1.github.io/ph-legislative-graph/) (GitHub Pages, served from `docs/`).
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

## Analyses

Phase 4 outputs live in `data/analytics/` and are committed:

- **Six co-authorship graphs** — `{house, senate} × {17th, 18th, 19th}`,
  kept separate per Congress and per chamber. Coalition structure in the
  17th Senate is not assumed comparable to the 19th House; keeping the
  graphs apart preserves honest temporal and cross-chamber comparisons.
  - `edges/{chamber}-{congress}.parquet` — `source`, `target`, `weight`
    (raw co-authorship count), `jaccard` (shared-bill overlap).
  - `nodes/{chamber}-{congress}.parquet` — per-legislator attributes:
    `bills_primary_filed`, `bills_coauthored`, `bills_solo_filed`,
    `bills_with_cosponsors_filed`, `avg_coauthors_per_filed_bill`,
    `coauthor_to_primary_ratio`.
  - `communities/{chamber}-{congress}.parquet` — Louvain assignment
    (`seed=42`) per legislator.
- **`graph_stats.csv`** — one row per graph: `nodes`, `edges`,
  `modularity`, `n_communities_all`, `n_communities_nontrivial` (≥ 2
  members), `isolated_fraction`.
- **`committee_funnel.csv`** — per chamber × scope (`17`, `18`, `19`,
  `combined`): `committee_name`, `assigned_bills`, `ras`, `rate`, and a
  `rank` within the chamber-scope cell.

**Model choices, stated plainly:**

- **Edges are co-authorship only.** The primary sponsor of a bill is
  *not* connected to that bill's co-authors. Primary sponsorship is a
  distinct legislative action, and letting it form edges would scale a
  legislator's degree with how many bills they file rather than with
  coalition behavior — Louvain would cluster by filing patterns, not
  by coalition. Primary filings and related scalars are preserved as
  node attributes instead, so the "solo filer" vs "coalition filer"
  signal survives without distorting community structure. Cost: bills
  with a single co-author contribute zero edges.
- **Isolated nodes are preserved.** A legislator with no co-authorship
  edges appears as a degree-0 node so the complete legislator set is
  retained. Senate graphs have high `isolated_fraction` because
  17th–19th Senate co-author data is sparse (see Limitations); this is
  honest data behavior, not a build defect.
- **Both raw count and Jaccard are stored per edge.** Louvain runs on
  raw count; Jaccard is available for downstream toggling.
- **Funnel rates are "committee touch rates," not "exit rates."** A
  bill assigned to *N* committees counts in each of the *N*. Rankings
  are computed only within each chamber × scope cell and only over
  committees with `assigned_bills ≥ 10`; small-n committees are
  excluded because a 2-bill, 2-RA committee is noise, not a 100%
  funnel. Special committees can lead a ranking at modest n; the
  touch-rate caveat applies.

Reproduce analytics with `python tasks.py analytics`. The site is assembled
by `python tasks.py site` from `data/analytics/` and published at
[csystm1.github.io/ph-legislative-graph](https://csystm1.github.io/ph-legislative-graph/)
via GitHub Pages (`docs/`, no database dependency). House graphs are rendered
as a 500-edge backbone in both the interactive and static views; the full
House graph is a near-complete graph (~90% of legislator pairs share bills)
and no browser view of that density is readable. Senate graphs are sparse
enough to render in full.

## Limitations

- **20th Congress excluded** — incomplete upstream; Phase 2 is 17th–19th Congress.
- **House co-author resolution** — per-Congress: 17th 97.33%, 18th 92.74%,
  19th 91.95%. Distinct unresolved: 10/19/28. Logged per-Congress to
  `outputs/house_unresolved_coauthors.csv`. Senate unresolved: 9/5/8 distinct
  in `outputs/senate_unresolved_coauthors.csv`. Floors: do not iterate.
- **Unmapped House committee codes** — 4 codes (`J506`, `K503`, `K504`,
  `K505`) contribute 35 fallback rows across 35 distinct House bills to
  `data/curated/committees.parquet` — each such bill carries exactly one
  unmapped code. Rows fall back to code-as-name in `bill_committees`.
  All four return 404 on
  `congress.gov.ph/committees/committee/view/{code}` (re-verified
  2026-10-19) — possibly retired committees or upstream typos. Accepted
  as-is; the mapping-extension task is closed. See
  `docs/data_contract.yaml` (`house_committee_fallback`) and
  `outputs/house_committee_missing_codes.csv`.
- **Sentinel bills** — 33 bills lack a primary authorship row; 20 lack any
  authorship. Same data-gap class across all three Congresses; `0000` /
  empty author codes upstream. Validation warns, not fails.
- **Untitled bills** — 10 bills lack all title fields (9 in the 18th, 1 in
  the 19th); fallback `"Untitled <bill_id>"` is used.
- **Upstream manual-encoding disclaimer** — `open-congress-data` is community-maintained
  and self-describes a manual-encoding process; parsed data inherits its
  accuracy characteristics.
- **Senate co-authorship is near-universal** — across the 17th–19th
  Congresses, Senate bills with ≥2 co-authors are common: 53 (17th),
  76 (18th), 104 (19th), out of 200 / 176 / 296 Senate bills with any
  co-author data. Under a co-authorship edge model this produces a
  complete or near-complete graph: senate-17 has 268 of 276 possible
  edges over 24 nodes (97.1% density, 0% isolated); senate-18 has 276
  (100%, 0% isolated); senate-19 has 276 (100%, 0% isolated). Modularity
  is near zero (0.012 / 0.0 / 0.008) because a complete graph admits no
  meaningful partition — not because of sparsity. House graphs, by
  contrast, show real community structure (modularity 0.05–0.10,
  3–4 nontrivial communities) over a much larger 301–316-member chamber.
  **An earlier version of this dataset reported a 17th-Congress sparsity
  anomaly (4 edges, 2 multi-coauthor bills, 83% isolated).** That was
  traced to co-author extraction gaps — the TOML `[[history]]` parser
  handled only one phrasing, the Senate entity matcher mishandled
  `FIRST MIDDLE LAST` prose (compound surnames, suffixes, mononyms), and
  `meta.authors_raw` lists were truncated to their first name so filing-
  line co-authors were silently dropped. All three were fixed this
  session; see "Data quality" below.
- **Data snapshot** — upstream HEAD `6e853ee`, 2025-10-16. Re-run
  `python -m src.ingest.fetch` to refresh.

### Data quality

- **Senate co-author extraction.** Senate co-authors are parsed from TOML
  `[[history]]` block prose (string-walk, no regex). The parser handles
  all 23 distinct co-author phrasings observed across the 17th–19th
  Congresses: self-request letters (`intention to be made co-author`,
  `requesting that he/she be made co-author`), passive clerk records
  (`Senator X was made co-author`, `Senators X, Y, and Z were made
  coauthors`), motions (`Motion of Senator X that he be made co-author`,
  `… together with Senators [list]`, `… namely [list]`), bulk
  (`All Senators present were made coauthors: [list]`), manifestations
  (`Senator X manifested the desire of Senator Y …`, `Manifestation of
  Senator X that Senator Y …`), and third-party requests
  (`Letter from Senator X … requesting that Senator Y be included`).
  The Senate entity matcher handles `FIRST MIDDLE LAST` prose, including
  compound surnames, suffixes, and mononyms. Post-fix resolution floors:
  **99.9% (17th) / 99.8% (18th) / 99.7% (19th)**. Residual: 12
  unresolved mentions across all three Congresses — corpus typos
  (`RIVILLA`, `TOLENTINO` without space) and extraction artifacts
  (`… on March 13, 2018`, `… has`, `… be`). Filing-line co-authors are also 
  extracted: `meta.authors_raw` is split into individual names 
  (both list and string forms) and positions 1..N become co_authors 
  with `resolution_method="senate_authors_raw/…"`, while
  position 0 remains the primary. One source-data anomaly is logged:
  **SBN-01470-19** lists 9 names in `authors_raw` but only 1 entry in
  `senate_website_author_codes` — the extraction uses the fuller
  `authors_raw` list, and the mismatch is surfaced as a warning at parse
  time. Bulk coauthorship motions ("all Senators present were made
  coauthors: [list]") are extracted in full, which is the primary driver
  of the near-complete Senate graphs (276 of 276 possible edges in the
  18th and 19th Congresses).

- **Senate community structure is not meaningful.** With 276 of 276
  possible co-authorship edges in the 18th and 19th Congresses, the
  Senate graph is a complete clique. Modularity (~0) reflects the absence
  of any possible partition, not a failure of the Louvain algorithm or a
  sign of sparse data. The Senate panels on the temporal page and the
  per-Congress graph pages are still rendered, but community detection
  is not informative for these chambers under the current co-authorship
  model.

- **House co-author pipeline.** The House pipeline is fully auditable:
  every raw coauthor mention either resolves to a unique legislator or
  is logged in `outputs/house_unresolved_coauthors.csv`. Resolution
  floors: **97.33% (17th) / 92.74% (18th) / 91.95% (19th)**. The
  residual gap between raw mentions and curated rows is fully accounted
  for by unresolved names plus legitimate deduplication (a House member
  listed twice in `congress_website_coauthors_raw`, or listed both as
  a coauthor and as the bill's primary author). No silent-drop path.
  This contrasts with the Senate pipeline before the extraction fix,
  where missed phrasings were silent — now both pipelines are auditable.

## Pipeline

```
fetch (sparse clone) → parse_toml → staging parquet
                     → validate → curated parquet
                     → load → Postgres
                     → graph build → communities, funnel
                     → build_site → docs/ (GitHub Pages)
```

## Reproducing

Full pipeline from a fresh clone:

```bash
cp .env.example .env           # first time only
python tasks.py pipeline       # fetch -> parse -> validate -> build-curated -> up -> load -> verify
```

Incremental workflow — each task is independently runnable:

```bash
python tasks.py fetch          # sparse-clone or refresh open-congress-data
python tasks.py parse          # TOML -> data/staging/*.parquet
python tasks.py validate       # pre-load gate; exit 0 = pass
python tasks.py build-curated  # staging + person TOMLs -> data/curated/*.parquet
python tasks.py up             # start Postgres container (waits for healthcheck)
python tasks.py load           # curated -> Postgres (idempotent upsert)
python tasks.py verify         # row counts per table
python tasks.py analytics      # curated -> data/analytics/ (graphs, communities, funnel)
python tasks.py site           # data/analytics -> docs/ (static site for GitHub Pages)
python tasks.py psql           # interactive shell for ad-hoc queries
```

`python tasks.py --help` lists all tasks.

## Reference

- Predecessor: [ph-republic-acts-de](https://github.com/csystm/ph-republic-acts-de) — TF-IDF + K-Means over Republic Acts.
- Upstream data: [bettergovph/open-congress-data](https://github.com/bettergovph/open-congress-data).