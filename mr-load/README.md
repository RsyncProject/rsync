# mr-load — Miraex → HubSpot loading system

`mr-load` replicates the proven **icalps loading system** for a new source estate
(Miraex): legacy CRM entities plus a document **library file system** whose index does
not exist yet — only a Google Drive account holds the material. The prior project
fetched files directly from a legacy database; this project instead **replicates the
Drive folder structure onto local disk first (via rclone)**, then reuses the exact
same downstream pattern: hierarchical path index + foreign-key association from each
file to the CRM entity it pertains to.

> Raw reconnaissance artifacts (portal IDs, Drive folder IDs, record IDs, contact
> data) are deliberately **kept out of this public repository**. They live in the
> operator's private recon digest and in the prior private repos. Placeholders in
> scripts are wired to a git-ignored `.env`.

## Sequential flow

1. **Phase 0 — Foundations**: decide the write path (mirror-Postgres vs direct API),
   create `miraex_*` reconciliation properties, discover portal association type IDs,
   stand up staging schema + ledgers, confirm sandbox.
2. **Phase 1 — Companies**: load all legacy Miraex companies into the CRM,
   **checked against potential duplication** (including pre-existing portal records).
3. **Phase 2 — Fair/exhibition contacts**: replicate incoming fair contacts,
   **not necessarily dedup-checked**, but always idempotent (deterministic keys).
4. **Phase 3 — Associations** (critical, autonomous agent): assess **cardinality**
   between the newly added entities and the **blast radius** within which loading is
   safe to perform with or without a sandbox; only then write, gated and previewed.
5. **Phase 4 — Deals**: Odoo exports CSV; straightforward *provided the custom
   property set is replicated first* and the pipeline/stage IDs exist in the portal.
6. **Phase 5 — Library**: `rclone` replicates the Drive tree to a local/dedicated
   Ubuntu server → classification script populates the ontology → hierarchical path
   index → FK association of each file to its company → two-phase upload with ledger.
7. **Phase 6 — Internal two-way sync**: if the above succeeds, it becomes the ground
   for an internal 2-way sync **agnostic of the previous stacksync pattern**.

Why rclone even though a bidirectionally-synced Postgres exists: downloading the
Drive directly would not preserve parts of the hierarchical structure we want to
keep, and precision is lower than the prior icalps loading. A synced local replica is
also a **safety net** for messy data, and the dedicated-server + classify + populate
approach is the seedbed for goal (7).

## Repository map

| Path | Purpose |
|---|---|
| `RETRIEVAL.md` | Synthesis of the past-project retrieval (the "memories") |
| `PLAN.md` | Phase-by-phase execution plan with gates and prior-art references |
| `AGENTS.md` | The agent team scaffold — one team per phase |
| `BINDING-QUESTIONS.md` | Open requirements that bind the ETL design (awaiting answers) |
| `scripts/rclone/` | Drive → local replication (skeleton, env-driven) |
| `scripts/indexer/` | Hierarchical path indexer + staging DDL (skeleton) |
| `ontology/` | Miraex ontology YAML skeleton for the `ontology_substrate` package |

## Safety model (inherited from icalps, non-negotiable)

- **Dry-run by default**; live writes only behind explicit per-phase approval gates
  (session-only env vars, `--approve-*` flags).
- **Preview == execution**: every write statement is composed from the same SELECT
  body used by read-only preview, so preview CSVs always match what would land.
- **Ledgers everywhere**: every direct API write is recorded in a Postgres ledger
  keyed by a deterministic legacy/source ID; re-runs are no-ops; `unmigrate` gives
  rollback.
- **Sandbox-first**: sandbox round-trip → 1-row prod pilot → 10-row batch → full run.
- **Portal constants are discovered, never copied**: association type IDs, pipeline
  and stage IDs, mirror column names are all portal-/connection-specific.
