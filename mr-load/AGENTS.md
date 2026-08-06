# mr-load agent teams

One team per part of the plan. Each team definition is written so it can be lifted
into `.claude/agents/*.md` (or a Workflow script) verbatim: mission, inputs, tools,
hard gates, and the artifact each agent must return. Teams share three standing
rules: (1) dry-run by default — a live write requires the phase gate to have been
explicitly approved by the operator; (2) every claim about the portal is *probed*,
never copied from icalps constants; (3) every agent's output is a reviewable
artifact (CSV/JSON/markdown), not just prose.

---

## Team 0 — Memory retrieval (COMPLETED)

The first task of the project: autonomous retrieval of the past project whose parts
relate to mr-load. Ten agents swept the five prior repos, the live portal, and the
Drive estate. Results: `RETRIEVAL.md` (sanitized) + the operator's private digest.
Re-run this team only if new prior-art repos surface.

---

## Team 1 — Companies

| Agent | Mission | Output artifact |
|---|---|---|
| `universe-builder` | Union Odoo export + Drive folder names + opportunity sheets into a master company list with source lineage and alias groups | `companies_master.csv` |
| `normaliser` | Bronze→silver: encoding repair, domain cleaning, +41 phone E.164, observed-vocabulary enum maps | `stg_company_normalised` + anomaly report |
| `dedup-prober` | Run the guardrail against a fresh portal export; calibrate thresholds on Miraex data; flag the pre-existing Miraex records and cleanup-workstream rows as protected | `dedupe_company.{csv,json}` |
| `gold-loader` | Preview + gated upsert keyed on `miraex_company_id` | preview CSV, run manifest |
| `verifier` | Re-extract by `miraex_company_id IS NOT NULL`, reconcile counts vs master list | reconciliation report |

Blocking rule: `gold-loader` may not run until `dedup-prober`'s artifact has been
reviewed by the operator and the review-band rows dispositioned.

## Team 2 — Fair/exhibition contacts

| Agent | Mission | Output artifact |
|---|---|---|
| `list-harvester` | Enumerate fair/tradeshow contact lists from the Drive trees; normalise each to the common contact schema; tag with event key (reuse the portal's existing `event_*` conventions) | per-event normalized CSVs |
| `key-minter` | Assign deterministic idempotency keys (source id, else `evt:<sha1(event\|email\|name)[:12]>`) — the sole duplicate protection when dedup is off | keyed contact set |
| `loader` | Batch upsert on the key; set marketing-contact status deliberately | run manifest |
| `idempotency-auditor` | Prove run-twice-is-a-no-op before the first live batch | audit note |

## Team 3 — Associations (the autonomous agent)

This is the critical team the plan calls for: it *decides*, then acts.

| Agent | Mission | Output artifact |
|---|---|---|
| `cardinality-assessor` | Measure real cardinality per entity pair from loaded staging data (distinct-count both directions); declare each edge in the ontology YAML with the measured cardinality | entity-pair inventory (workbook) |
| `portal-prober` | Discover actual association type IDs (both directions of every label), pipeline constraints, custom-label entitlement | portal constants file |
| `readiness-prober` | Per-edge FK resolution rate vs loaded parents; edges below the gate are deferred, not half-loaded | readiness report with PASS/BLOCK verdicts |
| `blast-radius-assessor` | BFS the FK graph to enumerate affected records; classify each edge `sandbox-required` / `prod-safe-gated` / `deferred`; populate the exemptions table with protected rows (pre-existing Miraex records, cleanup-flagged companies) | blast-radius verdict doc + exemptions CSV |
| `association-writer` | Only after operator approval per edge: preview CSVs from the exact insert SELECT, then gated writes with idempotency guards and a ledger | previews + run manifest |
| `metagraph-operator` | Iteratively re-probe fill rates while writes are throttled; stop-loss if a rate regresses | living metagraph workbook |

## Team 4 — Deals

| Agent | Mission | Output artifact |
|---|---|---|
| `export-negotiator` | Specify + validate the fresh Odoo export (record ids, dates, currency, stage vocabulary); inspect for the known CSV damage classes | export spec + damage report |
| `property-replicator` | Minimal property set under a `miraex` group; sandbox-first creation; record everything created | property manifest |
| `pipeline-builder` | Create Miraex pipeline(s) + stages; record IDs into the seed table | stage-map seed |
| `stage-mapper` | Two-field (stage, outcome) → stage-id matrix; fail-loud on unknowns | mapper + seed export |
| `loader` | Bronze watermark → silver → validation (REJECT missing company FK) → preview → gated upsert on `miraex_deal_id` | run manifest |
| `reconciler` | Post-load counts, amounts sum-check (unit trap!), owner resolution rate | reconciliation report |

## Team 5 — Library (rclone → index → FK → upload)

| Agent | Mission | Output artifact |
|---|---|---|
| `sync-operator` | rclone config + per-tree sync to the Ubuntu server; dry-run diff first; handle shared-item roots; decide native-Google-format export policy | sync log + tree stats |
| `indexer` | Walk the local tree; emit hierarchical path index rows with `fs:<sha1(relpath)[:12]>` ids; apply exclusion policy | `stg_library_normalised` rows |
| `fk-resolver` | Curated folder→company mapping + fuzzy assist for aliasing; unresolved folders skipped and reported, never guessed | mapping table + unresolved report |
| `ontology-populator` | Declare document entity + document→company edge; FilesystemConnector; probe fill rates | ontology YAML + probe results |
| `uploader` | Two-phase upload (file → note+association) with ledger, dry-run default, retry/backoff | ledger status report |
| `rollback-warden` | Keep `unmigrate` tested against the ledger before every scale-up step | rollback drill note |

## Team 6 — Internal two-way sync

| Agent | Mission | Output artifact |
|---|---|---|
| `adapter-author` | Implement the mr-load sync layer against `SyncProviderAdapter` (DirectAPI first) | adapter module |
| `outgoing-sync` | Hash-diff change detection → batch upsert by idProperty → id write-back | sync module + shadow-mode log |
| `incoming-sync` | `hs_lastmodifieddate` watermark poll → mirror upsert | sync module + shadow-mode log |
| `echo-guard` | `IS DISTINCT FROM`/hash guards + loop tests | test suite |
| `checkpoint-builder` | Make the sync checkpoint real (poller asserting hydration) | poller + gate wiring |
| `conflict-adjudicator` | Implement + test the declared conflict policy | policy tests |

---

## Team interaction contract

- Team 1 blocks Team 3 (companies must exist before association assessment) and
  Team 5's `fk-resolver` (mapping table targets `miraex_company_id`).
- Team 2 blocks Team 3 for contact edges only.
- Team 4 blocks Team 3 for deal edges only; Team 4's `property-replicator` and
  `pipeline-builder` block its own `loader`.
- Team 3's verdicts gate *all* association writes, including Team 5's uploader
  attach phase.
- Team 6 starts only after Team 5's acceptance gate passes (the "previous success"
  precondition for the internal sync).
