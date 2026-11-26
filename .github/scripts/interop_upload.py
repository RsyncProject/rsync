import argparse
import json
import os
from pathlib import Path
import re
import sys

from interop_network import MAX_RECORD_BYTES, NetworkError, download_records, list_artifacts


ARTIFACT_NAME = re.compile(r'interoperability-[a-z0-9-]+-attempt-[1-9][0-9]*')


def check_published_record(name, path, timeout, allow_missing=True):
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if (os.environ.get('GITHUB_REPOSITORY_ID') != '266877886'
            or not re.fullmatch(r'[1-9][0-9]*', run_id)
            or not ARTIFACT_NAME.fullmatch(name)):
        raise NetworkError('Invalid artefact publication context')
    if path.is_symlink() or not path.is_file():
        raise NetworkError('Local rendezvous record is not a regular file')
    with path.open('rb') as record_file:
        raw = record_file.read(MAX_RECORD_BYTES + 1)
    if not 0 < len(raw) <= MAX_RECORD_BYTES:
        raise NetworkError('Local rendezvous record exceeds its size limit')
    try:
        record = json.loads(raw)
    except (ValueError, RecursionError):
        raise NetworkError('Invalid local rendezvous record') from None
    if not isinstance(record, dict):
        raise NetworkError('Invalid local rendezvous record')
    listing = None
    if allow_missing:
        url = ('https://api.github.com/repos/RsyncProject/rsync/actions/runs/'
               + run_id + '/artifacts?per_page=100')
        listing, _ = list_artifacts(url, None)
        matches = [item for item in listing if item.get('name') == name]
        if len(matches) > 1:
            raise NetworkError('Duplicate rendezvous artefact')
        if not matches:
            return False
        if matches[0].get('expired') is not False:
            raise NetworkError('Invalid rendezvous artefact state')
    published = download_records(run_id, {name: None}, timeout=timeout,
                                 initial_listing=listing)[None][1]
    if published != raw:
        raise NetworkError('Published artefact differs from the local record')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('probe', 'require'))
    parser.add_argument('name')
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        present = check_published_record(args.name, args.path,
                                         timeout=10 if args.phase == 'probe' else 30,
                                         allow_missing=args.phase == 'probe')
        if args.phase == 'require' and not present:
            raise NetworkError('Rendezvous artefact is missing')
        if args.phase == 'probe':
            with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                output.write('present=' + str(present).lower() + '\n')
    except (OSError, ValueError, KeyError) as error:
        detail = str(error) if isinstance(error, NetworkError) else type(error).__name__
        print('Artefact publication failed: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
