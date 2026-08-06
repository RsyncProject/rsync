# mr-load execution plan

Each phase lists: objective, prior art to reuse, steps, and gates. Prior-art paths
refer to the private repos `ic-load`, `ic-d-load`, `icalps_etl`, `ic-ontology`.
Nothing writes to the live portal without passing its gates.

---

## Phase 0 — Foundations (blocking decisions + schema groundwork)

**Objective**: resolve the architectural forks and create the substrate every later
phase depends on.

1. **Write-path decision — RESOLVED (2026-08-06): Hybrid, like icalps.**
   Mirror-Postgres upserts for companies/contacts/deals (sync checkpoint +
   two-pass association bridge apply); direct API + ledger for library
   files/notes. Phase-0 consequence: stand up/confirm the Miraex sync connection
   and *discover* its mirror record-id columns before any association SQL is
   rendered.
2. **Reconciliation properties**: create unique custom properties
   `miraex_company_id`, `miraex_contact_id`, `miraex_deal_id` (string, always the
   *source-system* id — never the HubSpot record id; the prior project's semantics
   drifted between waves and that drift is expensive). One-time setup script or UI,
   never inside the data pipeline; record what was created.
3. **Portal discovery** (read-only): association type IDs per pair
   (`GET /crm/v4/associations/{FROM}/{TO}/labels`), pipeline/stage inventory,
   custom-object entitlement check (`/crm/v3/schemas`) — standard-tier portals may
   not allow custom objects, which keeps the library on the Files+Notes pattern.
4. **Staging schema**: `staging_miraex.*` tables — bronze hash tables, silver
   normalised tables, ledgers (clone `init_ledger.sql` shape), exemptions table.
5. **Sandbox**: confirm sandbox portal + token naming (`MIRAEX_SANDBOX_TOKEN` /
   `MIRAEX_PROD_TOKEN`), replicate token-precedence (sandbox wins when both set)
   and the `SandboxOverrideMap` keyed by source id.

**Gates**: portal constants captured into `ontology/miraex_ontology.yaml` (copied
from the committed skeleton; the filled file is git-ignored because it carries
probed portal constants); dry-run CLI validates it
(`python -m ontology_substrate probe --dry-run`).

---

## Phase 1 — Companies (dedup-checked)

**Objective**: every company from the Miraex legacy estate exists in the CRM exactly
once, carrying `miraex_company_id`, checked against duplication — including
pre-existing portal records (a Miraex company + contacts already exist) and the
in-flight duplicate-cleanup workstream.

**Prior art**: `pipeline/bronze.py` (watermark), `legacy/silver_normalise.py`
(value maps), `pipeline/dedupe.py` + `dedupe_probe.py` (guardrail), `sql/render.py`
(preview-wraps-execute upsert), `context/algorithms/company_siblings.py`.

1. **Company universe**: union of (a) Odoo partner/lead export, (b) the Drive
   per-company folder names, (c) the opportunity summary sheet. Build a curated
   master list with source lineage (the folder names double as the library FK
   universe in Phase 5 — same mapping table).
2. **Bronze**: CSV → DuckDB → md5 row-hash watermark (`NEW/MODIFIED/UNCHANGED`).
3. **Silver**: UTF-8/mojibake repair *before* any matching; domain cleaning;
   phone normalisation **re-parameterised for +41 default** (prior code assumed
   +33); enum value maps built from *observed* Odoo vocabulary, not idealized lists.
4. **Dedup guard**: score against a *fresh* portal export (staleness window is a
   known gap in the prior mechanism) with the weighted signals
   (domain/name/city/phone); handle the pandas `'.0'` id artifact; decide
   probe-only vs live-blocking (`BINDING-QUESTIONS.md` Q5). Route review-band
   hits to an operator artifact — never auto-merge.
5. **Gold**: upsert keyed on `miraex_company_id`; exclude sync-owned/HubSpot-owned
   columns from the update set; preview CSV before every live push.

**Gates**: dedup probe artifact reviewed; `--approve-gold` equivalent required;
sandbox round-trip first.

---

## Phase 2 — Fair/exhibition contacts (no dedup, but idempotent)

**Objective**: replicate contacts incoming from fairs and exhibitions. Dedup is not
required, but re-runs must not double-create.

**Prior art**: none for fairs (net-new). Reuse: sparse-required-field precedent
(PK-only mandate), person extraction column shape, bronze/silver conventions.
The portal already has an **unused `event_*` property set on contacts**
(`event_name` string; `event_type`/`event_id` enums; registration/attendance
dates; registration-status enum) — reuse and extend its enums rather than
minting new properties (`BINDING-QUESTIONS.md` Q6).

1. **Harvest**: fair contact lists live in the Drive tradeshow trees and
   mass-emailing sheets. Normalise each list to a common contact schema tagged
   with the event key (follow the portal's existing event-id naming convention).
2. **Idempotency key**: deterministic `miraex_contact_id` — from Odoo when present,
   else synthetic `evt:<sha1(event|email|name)[:12]>` (the walker-ID idiom). This
   is the *only* duplicate protection when dedup is off, so it is mandatory.
3. **Load**: batch upsert on the idempotency key; marketing-contact status set
   deliberately (billable-contact inflation risk).
4. **Company linkage** is deferred to Phase 3 (association assessment) — fair
   contacts may reference companies that Phase 1 dedup merged or skipped.

**Gates**: per-event dry-run counts reviewed; idempotency proven by run-twice test.

---

## Phase 3 — Associations (autonomous agent: cardinality + blast radius)

**Objective**: assess the cardinality that exists between the newly added entities
and the blast radius within which association loading is safe with or without a
sandbox; only then create associations, gated and previewed.

**Prior art**: two-pass bridge SQL + `NOT EXISTS` idempotency, pre-gold association
probe SQL (readiness thresholds), entity-pair inventory renderers (`ic-ontology`),
FK-cascade BFS (blast-radius enumeration), exemptions table + gated batch archive,
`SandboxOverrideMap`.

1. **Entity-pair inventory** (deliverable #1): every pair in scope
   (contact→company, deal→company, deal→contact, file-note→company, …) with
   cardinality measured *from the data* (distinct-count analysis both directions),
   the portal's actual type IDs (discovered, both directions of each label), and
   the chosen mechanism (scalar column vs association table vs v4 default).
2. **Readiness probe** (deliverable #2): per-edge FK resolution rate against loaded
   parents (the ≥90–95% gate pattern). An edge below threshold is deferred, not
   half-loaded — the prior project deferred an entire family at 99.2% unresolved.
3. **Blast-radius assessment** (deliverable #3): enumerate the affected-record set
   (BFS over the FK graph), classify writes as new-record-only vs touching
   pre-existing portal records (the existing Miraex company/contacts and the
   in-flight cleanup's flagged companies are protected rows → exemptions table).
   Verdict per edge: `sandbox-required` | `prod-safe-gated` | `deferred`.
4. **Write**: preview CSVs from the exact insert SELECT; `IS DISTINCT FROM` guards
   on any scalar-column updates; ledger per created association if via API.

**Gates**: operator approves the three deliverables; per-edge approval before write;
rollback path proven (archive window / unmigrate) before the first prod batch.

---

## Phase 4 — Deals (Odoo CSV; properties first)

**Objective**: import deals from the Odoo CSV export. Straightforward *iff* the
custom property set is replicated and pipeline/stage IDs exist first.

**Prior art**: fail-loud stage mapper + seed table, property-mapping registry,
owner resolution (<5% unresolved gate + fallback owner), bronze watermark,
`ON CONFLICT` upsert, import-flag failure catalog.

1. **Source — RESOLVED (2026-08-06): load from the existing Drive exports**
   (no fresh Odoo export for v1). Binding consequences: `miraex_deal_id` is a
   *synthetic deterministic key* (hash of normalised opportunity name + business
   line, frozen at first write); source duplicate rows deduped at silver
   (keep-latest); deal→company FK is resolved by *name* against the Phase 1
   company master with mandatory review of fuzzy matches; missing dates accepted
   as lossy. A future ID-bearing export reconciles onto these keys — it never
   replaces them.
2. **Properties first**: declare the Miraex deal property set minimally (property
   sprawl was a real failure mode — a later cleanup had to delete dozens);
   create under a dedicated property group with the `miraex_*` prefix; the
   legacy-id property is immutable and survives any future cleanup.
3. **Pipeline/stages**: create the Miraex pipeline(s) (brand-prefixed naming
   precedent); record stage IDs into the seed table; **map by ID, never by label**
   (existing portal has duplicate/dirty stage labels).
4. **Stage mapping**: two-field mapping (stage + outcome/status) → stage id;
   unknown combos raise. Confirm amount unit/currency explicitly (k€ 1000×
   inflation and unconverted multi-currency are documented prior failures).
5. **Load**: bronze watermark → silver (dates to ISO, numerics NULL-cleaned,
   owner emails resolved) → validation gate (REJECT on unresolvable company FK,
   WARN on contact FK) → gold upsert keyed on `miraex_deal_id` → associations via
   Phase 3 machinery.

**Gates**: property/pipeline creation is sandbox-first with typed confirmation for
production; validation STOP checks; preview before upsert.

---

## Phase 5 — Library (rclone → index → FK → upload)

**Objective**: replicate the Drive folder structure locally, index it
hierarchically by path, link each file to its company, and load files into the CRM
using the proven two-phase pattern.

**Prior art**: the entire `library_files` package (walker, silver normaliser +
DDL, FK view, two-phase uploader, ledger, gates, unmigrate) — source-agnostic once
files are local; materialized-path hierarchy schema if a first-class tree is wanted.

1. **rclone replication** (`scripts/rclone/`) — **scope RESOLVED (2026-08-06):
   per-company tree only for v1** (per-opportunity + tradeshow trees deferred;
   tradeshow material still feeds Phase 2 directly). Sync onto the Ubuntu server
   with `--drive-export-formats` for native Google files (provisional
   docx/xlsx/pptx default pending the Library binding question) and
   checksum-based re-runs; dry-run diff reviewed before the first live sync.
2. **Classification/index** (`scripts/indexer/walk_index.py`): walk the local tree
   (sorted, deterministic), emit one row per file: synthetic stable id
   `fs:<sha1(tree/relpath)[:12]>` (tree-prefixed so identical relative paths in
   different trees cannot collide), normalised relative path, name, size, mtime, the
   *company key extracted from the path* (top-level folder segment), and an
   exclusion flag (extension/size policy). Upsert into
   `staging_miraex.stg_library_normalised` (DDL in `scripts/indexer/`).
3. **Folder→company FK resolution**: curated mapping table (folder name →
   `miraex_company_id`) + normalised/fuzzy assist for the known aliasing
   (`YYYY_` prefixes, trailing spaces); unresolved folders are **skipped and
   reported, never guessed** (the prior all-or-nothing group rule). The ontology
   ProbeEngine measures the fill rate (fraction of files resolved) as the
   go/no-go metric.
4. **Ontology population**: declare the filesystem as a bronze layer; `document`
   entity + `document→company` edge in `ontology/miraex_ontology.yaml`; a
   `FilesystemConnector` (small `LayerConnector` subclass) lets the ProbeEngine
   compute layer-by-layer rates; operator drives loading off the living metagraph.
5. **Upload**: two-phase (file → note+association via v4 default endpoint) with the
   ledger, dry-run default, retry/backoff, `unmigrate` rollback; note body carries
   the original relative path so the hierarchy is visible on the record.

**Gates**: rclone dry-run diff reviewed; index fill-rate threshold; sandbox
round-trip; 1-row prod pilot → 10-row → full; ≥99% attached acceptance.

---

## Phase 6 — Internal two-way sync (goal: stacksync-agnostic)

**Objective**: generalise the pipeline into an internal 2-way sync so the pattern
no longer depends on the previous vendor mirror.

**Prior art**: `ontology_substrate.adapters.SyncProviderAdapter` ABC (+ the
DirectAPI reference impl), the row-hash change-detection pattern, the
`IS DISTINCT FROM` echo guard, the (never-implemented) sync-checkpoint poller seam,
declared conflict policy (`prefer_record_with_most_metadata`).

Build order: mirror schema lifecycle → outgoing sync (hash-diff → batch upsert by
idProperty → write back returned ids) → incoming sync (poll `hs_lastmodifieddate`
watermark → upsert mirror) → echo-loop prevention → conflict policy → make the
sync checkpoint *real* (poller asserting zero un-hydrated rows).

**Gates**: shadow mode (log-only) before any write path activates; per-entity
enablement; full ledger.
