-- mr-load library staging: hierarchical path index + FK resolution + ledgers.
-- Ledgers and exemptions mirror the proven icalps DDL with miraex_* naming; the
-- path-index table adapts the icalps silver contract for a walked filesystem
-- (no DB-supplied metadata), borrowing the materialized-path idiom from the
-- prior hierarchy schema. {schema} is substituted by the loader via
-- allowlist-validated replace (never str.format — literal braces in SQL
-- comments break it; a prior-project lesson).

CREATE SCHEMA IF NOT EXISTS {schema};

-- The path index. One row per file in the rclone mirror; hierarchy carried by
-- relative_path/path_segments (materialized-path style: segments '|'-joined,
-- queryable via string_to_array). Load walk_index.py CSVs with an explicit
-- column list (the table interposes resolver columns absent from the CSV):
--   COPY {schema}.stg_library_normalised (miraex_doc_id, tree, relative_path,
--     file_name, path_segments, depth, company_key_raw, file_size, modified_at,
--     excluded) FROM ... WITH (FORMAT csv, HEADER)
CREATE TABLE IF NOT EXISTS {schema}.stg_library_normalised (
    miraex_doc_id      TEXT PRIMARY KEY,           -- fs:<sha1(tree/relpath)[:12]>
    tree               TEXT NOT NULL,               -- companies|opportunities|tradeshows
    relative_path      TEXT NOT NULL,
    file_name          TEXT NOT NULL,
    path_segments      TEXT NOT NULL,
    depth              INTEGER NOT NULL,
    company_key_raw    TEXT,
    miraex_company_id  TEXT,                        -- resolved by the FK resolver
    resolution_method  TEXT,                        -- mapping|exact|fuzzy_reviewed|NULL
    file_size          BIGINT,
    modified_at        TIMESTAMPTZ,
    excluded           TEXT NOT NULL DEFAULT '',    -- ''|ext|oversize|no_company
    _indexed_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stg_library_company
    ON {schema}.stg_library_normalised (miraex_company_id);
CREATE INDEX IF NOT EXISTS idx_stg_library_company_key
    ON {schema}.stg_library_normalised (company_key_raw);

-- Curated folder->company mapping (the aliasing across trees — YYYY_ prefixes,
-- trailing spaces — makes a reviewed table mandatory; unresolved folders are
-- skipped and reported, never guessed).
CREATE TABLE IF NOT EXISTS {schema}.map_folder_company (
    tree               TEXT NOT NULL,
    company_key_raw    TEXT NOT NULL,
    miraex_company_id  TEXT NOT NULL,
    confidence         TEXT NOT NULL DEFAULT 'curated',  -- curated|fuzzy_reviewed
    reviewed_by        TEXT,
    reviewed_at        TIMESTAMPTZ,
    PRIMARY KEY (tree, company_key_raw)
);

-- Two-phase upload ledgers (clone of the proven icalps shape): phase 1 file
-- upload, phase 2 note+association. Re-runs skip rows already succeeded;
-- unmigrate reads status='attached' as its rollback index.
CREATE TABLE IF NOT EXISTS {schema}.fct_files_uploaded (
    miraex_doc_id    TEXT PRIMARY KEY,
    hs_file_id       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
        -- pending|uploaded|failed|dry_run
    error            TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS {schema}.fct_file_notes_posted (
    miraex_doc_id    TEXT PRIMARY KEY,
    hs_note_id       TEXT,
    idempotency_key  TEXT NOT NULL,               -- 'miraex_libfile_'||miraex_doc_id
    status           TEXT NOT NULL DEFAULT 'pending',
        -- pending|attached|partial|failed|dry_run|unattached_via_unmigrate
    error            TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    first_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_attempt_at  TIMESTAMPTZ
);

-- Blast-radius exemptions (Phase 3): records protected from any bulk operation.
CREATE TABLE IF NOT EXISTS {schema}.fct_cleanup_exemptions (
    object_type  TEXT NOT NULL,
    hubspot_id   TEXT NOT NULL,
    legacy_id    TEXT,
    label        TEXT,
    reason       TEXT,
    source       TEXT NOT NULL,                   -- e.g. 'mr_load_blast_radius_v1'
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, hubspot_id)
);
