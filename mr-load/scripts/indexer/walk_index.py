#!/usr/bin/env python3
"""mr-load Phase 5 step 2: hierarchical path index over the rclone-synced tree.

Replicates the icalps library silver contract for a source with no DB metadata:
walk the local mirror deterministically, emit one row per file with a stable
synthetic id (``fs:<sha1(relpath)[:12]>`` — the prior walker.py idiom), the
normalised relative path (the hierarchical index), and the company key extracted
from the path (top-level folder segment by default). Output is a CSV shaped for
``init_silver_library.sql``'s staging table; loading and FK resolution against
``miraex_company_id`` happen downstream.

Usage:
  walk_index.py --root /srv/mr-load/drive/companies --tree companies -o index.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# Extension exclusion mirrors the prior project's image-exclusion set; extend
# per the Phase 5 exclusion-policy decision (BINDING-QUESTIONS.md).
EXCLUDED_EXTS = {".ds_store", ".ini", ".db", ".lnk", ".tmp"}
# HubSpot file-upload hard cap; oversize files are indexed but flagged.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

FIELDS = [
    "miraex_doc_id",      # fs:<sha1(relpath)[:12]> — deterministic, re-run stable
    "tree",               # which mirror tree (companies|opportunities|tradeshows)
    "relative_path",      # normalised dir path, '/'-separated, no leading slash
    "file_name",
    "path_segments",      # '|'-joined segments: the materialized-path index key
    "depth",
    "company_key_raw",    # top-level segment — resolved to miraex_company_id later
    "file_size",
    "modified_at",
    "excluded",           # 'ext' | 'oversize' | '' (loadable)
]


def synthetic_id(relpath: str) -> str:
    return "fs:" + hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:12]


def walk(root: Path, tree: str):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        relposix = rel.as_posix()
        segments = relposix.split("/")
        stat = path.stat()
        excluded = ""
        if path.suffix.lower() in EXCLUDED_EXTS:
            excluded = "ext"
        elif stat.st_size > MAX_UPLOAD_BYTES:
            excluded = "oversize"
        yield {
            "miraex_doc_id": synthetic_id(relposix),
            "tree": tree,
            "relative_path": "/".join(segments[:-1]),
            "file_name": segments[-1],
            "path_segments": "|".join(segments[:-1]),
            "depth": len(segments) - 1,
            "company_key_raw": segments[0] if len(segments) > 1 else "",
            "file_size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "excluded": excluded,
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--tree", required=True,
                    choices=["companies", "opportunities", "tradeshows"])
    ap.add_argument("-o", "--output", required=True, type=Path)
    args = ap.parse_args()

    if not args.root.is_dir():
        ap.error(f"not a directory: {args.root}")

    rows = list(walk(args.root, args.tree))
    with args.output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    loadable = sum(1 for r in rows if not r["excluded"])
    companies = {r["company_key_raw"] for r in rows if r["company_key_raw"]}
    print(f"{len(rows)} files indexed ({loadable} loadable, "
          f"{len(rows) - loadable} excluded), "
          f"{len(companies)} distinct top-level company keys -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
