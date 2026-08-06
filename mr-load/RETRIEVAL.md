# Past-project retrieval — the icalps loading system memories

Ten retrieval agents reconstructed the prior project from five repos
(`icalps_pipeline`, `ic-load`, `ic-d-load`, `icalps_etl`, `ic-ontology`), the live
HubSpot portal, and Google Drive. This file is the sanitized synthesis; the raw
digest (with IDs and record-level findings) is retained privately by the operator.

## 1. Library/document loading — the pattern mr-load replicates

- **Bronze**: the legacy `Library` table held only *metadata* per file (relative
  directory path + filename + FKs to company/contact/deal). Binaries were never
  fetched from the DB — they were read from a local mount (`LIBRARY_BASE_DIR` +
  relative path). **mr-load's rclone-synced Drive replaces exactly this contract.**
- **Silver (the hierarchical path index)**: `ic-load/pipeline/library_files/silver_library.py`
  normalises paths (backslash→slash, trim), filters (explicit-inactive, deleted,
  missing PK/path, no-FK "global template" rows) and upserts into
  `staging.stg_library_normalised` (PK legacy id; indexed FK columns; path + name
  NOT NULL). The hierarchy is implicit in the normalised relative path. An explicit
  **materialized-path hierarchy** template also exists
  (`sql/silver/00_hierarchy_schema.sql`: `node_key/parent_key/depth/path_key/path_array`
  + recursive-CTE builders) if mr-load wants a first-class folder tree.
- **FK reconciliation**: a plain SQL view joins silver rows to the mirror tables on
  the *legacy-key custom property* stamped on every CRM record at load time
  (`icalps_company_id` etc. → mr-load: `miraex_company_id`). Exact-id equality only;
  precondition is that parent entities were already loaded carrying that property.
- **HubSpot side**: *not* a custom object, *not* a folder replica. Two-phase per file:
  (1) upload to Files API (all files flat in one `/legacy_migrations` folder — the
  hierarchy survives only in Postgres); (2) create a Note carrier
  (`hs_attachment_ids`, semicolon-delimited) and attach via the **v4 default
  association endpoint** (singular type names, no type-ID lookup needed).
- **Idempotency/safety**: two ledger tables keyed by legacy file id
  (`fct_files_uploaded`, `fct_file_notes_posted`) with status machine
  `pending|uploaded|attached|partial|failed|dry_run`, attempt counters, skip-sets on
  re-run, retry w/ backoff honouring `Retry-After`, env-var approval gates
  (dry-run default), and an `unmigrate` rollback subcommand.
- **`walker.py`** provides pure-filesystem enumeration with deterministic synthetic
  IDs `fs:<sha1(relpath)[:12]>` — the exact bridge for a tree with no DB metadata.

## 2. Company loading + dedup

- Medallion flow: CSV → DuckDB bronze with **md5 row-hash watermark**
  (`NEW/MODIFIED/UNCHANGED` vs a persisted hash table) → silver rename/value-maps
  (status/type enums, country ISO, LinkedIn URL canonicalisation, E.164 phones —
  **hardcoded French +33; must be re-parameterised for Swiss data**) → gold
  `INSERT … ON CONFLICT (legacy_id) DO UPDATE` into the mirror table.
- **DedupeGuardrail**: candidates (NEW/MODIFIED) scored against a *fresh portal
  export* after aggressive normalisation (mojibake repair → NFKD → lowercase →
  charset strip; corporate-stopword name roots; float-artifact id fix). Score =
  0.45·domain + 0.35·name-Levenshtein + 0.10·city + 0.10·phone; review ≥0.65,
  block ≥0.82. In icalps it was **probe-only** — production duplicate protection was
  watermark + unique-key upsert. mr-load must decide whether the guard goes
  live-blocking.
- Sibling/hierarchy "domain hack": plural domain groups get a canonical parent
  (3-tier deterministic selection) and children with synthetic `N.domain` domains;
  cross-group fuzzy matches are review-only, never auto-merged.

## 3. Associations, cardinality, blast radius

- Two mechanisms coexisted: (a) **SQL bridge** into mirrored association tables
  (two-pass FK resolution: sync-provider record-UUID pass, legacy-id fallback pass,
  `UNION` + triple-key `NOT EXISTS` for idempotency); (b) **direct v4 API** (default
  associations for note carriers; USER_DEFINED labels for company parent/child with
  type IDs in the request *body*).
- **Hard ordering invariant**: gold upsert → sync checkpoint (IDs hydrated) →
  association writes. Running associations early silently matches nothing.
- **Cardinality** was declared per edge in entity cards / ontology YAML
  (1:N contact→company enforced structurally via the scalar `associatedcompanyid`
  column; N:M engagements via association tables) and *validated* upstream with
  per-edge FK violation policies (REJECT vs WARN) + strict import order
  Company → Contact → Deal → Communication.
- **Blast-radius practice**: read-only probes measuring per-edge FK/UUID readiness
  *before* any write (an association family stays disabled until readiness clears a
  threshold — one family was deferred entirely at 99.2% unreconciled); preview CSVs
  from the exact insert SELECT; approval gates; an **exemptions table** protecting
  records from bulk operations; gated batch archive (100 ids/call) as rollback.
- **Sandbox practice**: a dedicated sandbox portal with token precedence (sandbox
  wins when both set), a `SandboxOverrideMap` keyed by *legacy* id (stable across
  portals) so prod ids never hit sandbox calls, and `unmigrate` to clear state
  between probe iterations.

## 4. Deals + custom property replication

- One-time setup (custom properties, pipelines + stages) is **UI/one-time-script
  territory, never inside the data pipeline**; created IDs are then recorded into a
  seed table + a fail-loud Python stage mapper ((pipeline, stage, outcome) triple →
  portal stage id; unknown combos raise, never silently NULL).
- Hard-won failure catalog: two-field stage mapping (stage + outcome) or deals go
  invisible in board view; k€-vs-€ 1000× inflation; `'N/A'` strings in numerics;
  integer legacy user FK ≠ HubSpot owner (resolve via owners email lookup, <5%
  unresolved gate, named fallback owner); `createdate` cannot be written; legacy-id
  property is VARCHAR in HubSpot (cast with regex guard on read-back).
- The prior legacy CSVs were booby-trapped (BOM, literal `NULL`, Excel-mangled
  dates, multiline notes, sentinel ints, headerless FK file, embedded HTML) —
  inspect the Odoo export for the same damage classes before designing the loader.

## 5. Sync architecture + state

- Gold writes were `INSERT … ON CONFLICT` into a **sync-provider-mirrored Postgres**
  (`hubspot.*` schema) — writing the mirror *is* writing HubSpot after sync. The
  pipeline-owned `staging.*` schema (ledgers, hashes) never propagates.
- The sync checkpoint stage was a **stub** (assumed-mode env var); latency was
  absorbed by the two-pass bridge. Mirror record-id column names are
  per-connection random suffixes — discovered constants, never copied.
- Echo-loop prevention: every mirror UPDATE guarded with `IS DISTINCT FROM`.
- Runner: stage machine with JSON artifact per run, `--resume-from`, per-stage
  SKIPPED/WARNING/FAILED, `--preview`, `--approve-gold` hard gate. (Known drift bug:
  the archived runner references two stages/hooks that don't exist in its enum —
  reconcile before reuse.)
- **Goal (7) seam already exists**: `ontology_substrate.adapters` defines
  `SyncProviderAdapter` (mirror column per entity, boundary drift modes) with
  `StackSyncProviderAdapter` and `DirectAPISyncProvider` reference implementations —
  code mr-load against the ABC, not against any vendor's column names.

## 6. Ontology substrate (classification layer)

- `ontology_substrate` (installable package) = declarative YAML source of truth
  (layers, entities, edges with per-layer FK columns + cardinality + explicitly
  paired bidirectional type IDs, property lineage with a closed 7-flag drift
  vocabulary) + a **ProbeEngine** whose unit of work is one edge, computing
  layer-by-layer fill rates through pluggable `LayerConnector`s (CSV, Postgres —
  a `FilesystemConnector` for the rclone tree is a ~30-line subclass) + four
  renderers incl. a "living metagraph" the operator sorts by live fill-rate while
  throttling association writes.
- The established pattern: substrate = library; execution scripts live per-project.
  The Ubuntu-server classification script is exactly such a script.

## 7. Live-system findings (details in private digest)

- **Portal**: standard-tier account; **no custom objects exist** (and the tier may
  not allow them — verify entitlement before designing a library custom object).
  The icalps footprint is namespace-prefixed snapshot properties per object, an
  origin-discriminator enum, a dedicated brand-prefixed deal pipeline, default
  association types only. A **pre-existing Miraex company with a handful of
  associated contacts is already in the portal** — day-one dedup targets. A
  duplicate-cleanup workstream is mid-flight on ~2.3k companies (staging properties
  on records) — mr-load must coordinate, not fight it. An **unused `event_*`
  fair/exhibition property set already exists on contacts** and can be reused.
  Legacy-id property semantics drifted between load waves (external id vs own
  record id) — mr-load must pin "always the source-system id" as a hard rule.
- **Drive**: the Miraex sales estate has a numbered filing scheme with (a) a
  per-company folder tree (~57 folders), (b) a per-opportunity tree (~75
  `YYYY_Partner` folders, shared item-by-item — parents invisible; rclone needs
  per-folder roots or drive membership), (c) per-tradeshow folders, and (d) Odoo
  `crm.lead` exports that are **thin and dirty** (no record IDs, no dates,
  duplicate rows, inconsistent stage labels, revenue as formatted text) — a fresh
  ID-bearing export is strongly indicated. Company-name aliasing across trees
  (trailing spaces, `YYYY_` prefixes) means folder→company matching needs a curated
  mapping table + fuzzy review, mirroring the prior bronze workbook approach.
  No mr-load planning doc exists anywhere yet — this scaffold is it.

## 8. What has no prior art (net-new for mr-load)

- rclone / Google Drive replication (upstream of the walker).
- Odoo as a source (export shape, stage vocabulary, currencies).
- Fair/exhibition contact ingestion (keyword hits in the old repos were noise).
- The folder→company FK convention (the old system had DB-supplied FKs per file;
  Miraex FKs must be *derived from the path hierarchy*).
