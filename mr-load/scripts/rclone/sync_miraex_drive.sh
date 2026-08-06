#!/usr/bin/env bash
# mr-load Phase 5 step 1: replicate the Miraex Drive trees to local disk.
#
# Dry-run by default — pass --live to actually sync (mirrors the pipeline-wide
# dry-run-first gate). Folder IDs and paths come from .env (git-ignored); see
# .env.example. Trees shared item-by-item have no visible parent, so each tree
# gets its own remote with root_folder_id rather than one remote for the drive.
#
# Usage:
#   ./sync_miraex_drive.sh            # dry-run diff of every configured tree
#   ./sync_miraex_drive.sh --live     # real sync
#   ./sync_miraex_drive.sh --tree companies [--live]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$HERE/.env" ] && . "$HERE/.env"

: "${MRLOAD_LOCAL_ROOT:?set MRLOAD_LOCAL_ROOT in .env (e.g. /srv/mr-load/drive)}"
: "${RCLONE_CONFIG:=$HERE/rclone.conf}"
export RCLONE_CONFIG

# tree name -> remote name (defined in rclone.conf with its own root_folder_id)
declare -A TREES=(
  [companies]="miraex-companies:"      # per-company folder tree
  [opportunities]="miraex-ongoing:"    # per-opportunity YYYY_Partner tree
  [tradeshows]="miraex-tradeshows:"    # per-event tree
)

LIVE=0
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --live) LIVE=1 ;;
    --tree) ONLY="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

FLAGS=(
  --checksum            # re-runs compare content, not just mtime/size
  --create-empty-src-dirs  # empty folders are part of the hierarchy we preserve
  --drive-export-formats "docx,xlsx,pptx"  # native Google files -> Office
  --exclude-from "$HERE/exclude.txt"
  --transfers 4 --checkers 8
  --log-level INFO
)
[ "$LIVE" -eq 1 ] || FLAGS+=(--dry-run)

for tree in "${!TREES[@]}"; do
  [ -n "$ONLY" ] && [ "$tree" != "$ONLY" ] && continue
  dest="$MRLOAD_LOCAL_ROOT/$tree"
  mkdir -p "$dest"
  echo "=== ${TREES[$tree]} -> $dest (live=$LIVE)"
  rclone sync "${TREES[$tree]}" "$dest" "${FLAGS[@]}" \
    --log-file "$MRLOAD_LOCAL_ROOT/logs/sync_${tree}_$(date -u +%Y%m%dT%H%M%SZ).log"
done

echo "done. next: scripts/indexer/walk_index.py --root $MRLOAD_LOCAL_ROOT"
