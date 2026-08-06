# Binding requirements — open questions for the mr-load ETL pipeline

Distilled from the past-project retrieval. Each answer *binds* a design decision;
the four starred questions are the critical path and were put to the operator
directly. Answers get recorded here with a date.

## ★ Q1 — Write path (the biggest fork)

Does mr-load write through a sync-provider-mirrored Postgres (icalps mechanism:
`INSERT … ON CONFLICT` into mirror tables, sync checkpoint, two-pass association
bridge) or directly via the HubSpot API (batch upsert by unique idProperty, v4
association API, per-object ledgers)? Hybrid (mirror for CRM objects, direct API
for files/notes) is the icalps precedent.
**Binds**: the entire gold layer, association mechanism, checkpoint stage, goal-7
scope.
**Answer (2026-08-06)**: **Hybrid, like icalps** — mirror-Postgres upserts for
companies/contacts/deals; direct API + ledger for library files/notes. Follow-up
prerequisite: a sync connection for the Miraex objects must exist, and its mirror
record-id columns + association type IDs are *discovered* constants (Phase 0).

## ★ Q2 — Target portal + sandbox

Same portal as the prior loading (where a Miraex company + contacts already exist,
implying brand-tagging + dedup-merge decisions) or a separate Miraex portal? Is a
sandbox portal available for the sandbox-first ritual?
**Binds**: dedup scope, origin tagging, property/pipeline naming, every discovered
constant.
**Answer (2026-08-06)**: **Same portal, sandbox-first** — every phase rehearses in
the sandbox portal (round-trip → 1-row prod pilot → 10-row → full). The
pre-existing Miraex records and the in-flight cleanup workstream are therefore
in-scope dedup/blast-radius concerns from day one.

## ★ Q3 — Odoo export quality

Fresh ID-bearing export (crm.lead + partner references with database ids, created
dates, currency, full stage vocabulary) or must the pipeline load from the stale
Drive spreadsheets (no record IDs, no dates, duplicates, dirty labels)?
**Binds**: `miraex_deal_id` viability, watermark/delta capability, FK resolution
strategy, timeline.
**Answer (2026-08-06)**: **Load from the existing Drive files.** Consequences now
binding: `miraex_deal_id` is *synthetic* (deterministic hash of normalised
opportunity name + business line — frozen once first written), deal→company FK
resolution is name-based against the company master with mandatory review of
fuzzy matches, duplicate rows in the source must be deduped at silver with a
keep-latest rule, and dates/create-date preservation are accepted as lossy.
Re-runs remain safe only through the synthetic-key upsert; a later fresh export
would be reconciled *onto* these keys, never replace them.

## ★ Q4 — Library scope + folder→company convention

Which Drive trees are in scope for the rclone mirror (per-company tree /
per-opportunity tree / tradeshows / all), and how does a path resolve to a company
(curated mapping table — recommended given known aliasing — vs pure fuzzy match)?
Will drive membership be granted so shared-item trees have stable parents?
**Binds**: rclone config, indexer key extraction, FK resolver design, fill-rate
gate.
**Answer (2026-08-06)**: **Per-company tree only (v1)**, top-level folder segment
= company key, resolved through a curated folder→`miraex_company_id` mapping
table with fuzzy-assist review; unresolved folders skipped and reported. The
per-opportunity and tradeshow trees are out of the v1 mirror (tradeshow material
still feeds Phase 2 contact harvesting directly from Drive).

## Remaining binding questions by phase

### Foundations
- Credentials: is a Private App token with `crm.schemas.*`, `crm.objects.*`,
  `files` scopes available? (The MCP connection used for recon cannot create
  schemas/notes/files.)
- Custom-object entitlement on the portal tier — decides whether a document object
  is even possible, else Files+Notes stands.
- Which Postgres instance hosts `staging_miraex.*` (ledgers, hashes, index)?

### Companies
- **Q5** — Dedup guard live-blocking (block ≥0.82 / review ≥0.65) or probe-only?
  Thresholds recalibrated on Miraex data?
- Authoritative dedup key hierarchy: `miraex_company_id` exact → domain → fuzzy?
- Disposition of the pre-existing Miraex records: merge targets, enrich-canonical,
  or excluded?
- Coordination rule with the in-flight duplicate-cleanup workstream (do not fight
  its staging properties / exemptions list needed).
- Sibling/parent-child hierarchy needed at all, or flat account universe?
- Phone default region +41 confirmed? Multi-country sources needing libphonenumber?
- Fallback owner for unresolved rows; owner emails provisioned as portal owners?
- One-shot or recurring (watermark hash tables kept or dropped)?

### Fair/exhibition contacts
- Scope: which fairs/lists; Sensing business line in or out?
- **Q6** — Reuse the existing `event_*` contact properties + extend enums, or new
  `miraex_*` event properties?
- Marketing-contact status policy (billable-contact inflation).
- Confirm: dedup truly off even when emails collide with existing contacts?

### Associations
- Which pairs are in scope, and per pair: default labels (type-ID-free endpoint)
  or custom labels (portal discovery + v4 with IDs in body)?
- Numeric gates: FK-resolution ≥90%? match-rate ≥95%? Defer-below threshold?
- Fair-contact→company cardinality: strict 1:N (scalar column) or N:M (tables)?
- Rollback story: ledger + gated batch archive + exemptions from day one?

### Deals
- Pipelines: new brand-prefixed Miraex pipeline(s) cloned from which stage
  template? One per business line?
- Which revenue field maps to `amount`, in which currency (CHF/EUR/USD all appear);
  raw currency into native `amount` vs k-unit custom fields (the prior k€ trap)?
- Which Odoo date maps to `closedate`; timezone; is create-date preservation
  required (impossible via the standard upsert path)?
- Property set: minimal list, group name, and the immutable legacy-id property.

### Library
- rclone cadence: one-shot mirror or recurring sync (incremental re-index +
  re-upload policy)? Ledger keyed on Drive file id or path hash?
- Native Google formats (Docs/Sheets): export to Office/PDF or skip?
- Exclusion policy: extensions, size caps (portal upload limit), folders with no
  company match.
- Target model confirmed: Files+Notes with path in note body (custom object not
  available on current tier)?
- Note→deal associations too (per-opportunity tree), or company-only?

### Two-way sync
- Conflict policy: HubSpot-wins / Postgres-wins / field-level merge /
  prefer-most-metadata (the prior declared policy)?
- Change detection: row-hash diff, `updated_at` triggers, or logical replication?
- V1 scope: true 2-way or one-way load + periodic reconciliation?

### Cross-cutting
- Encoding gate: can every source export be validated UTF-8 before load
  (the prior project suffered irreversible mojibake)?
- Audit trail: seed a decisions/observations audit DB from day one (the prior
  operator mandated it)?
- Operator authorization protocol carried over verbatim: no schema-changing or
  destructive operation without per-operation approval; deletions only via REST
  archive (never SQL DELETE against a mirror).
