from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .process import file_digest
from .results import unique_object

@dataclass(frozen=True)
class Profile:
    name: str
    version: Optional[str]
    protocol: Optional[int]
    not_applicable: dict
    unsupported: dict

def _mapping(path, field, values):
    if not isinstance(values, dict):
        raise ValueError(f'{path}: invalid {field}')
    if not all(isinstance(key, str) and key and isinstance(value, str) and value
               for key, value in values.items()):
        raise ValueError(f'{path}: invalid {field}')
    return dict(values)

def load_profile(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(data, dict):
        raise ValueError(f'{path}: profile must be an object')
    if set(data) - {'version', 'protocol', 'not_applicable', 'unsupported'}:
        raise ValueError(f'{path}: unknown profile field')
    version = data.get('version')
    if version is not None and (not isinstance(version, str)
                                or not re.fullmatch(r'\d+(?:\.\d+)+', version)):
        raise ValueError(f'{path}: invalid version')
    protocol = data.get('protocol')
    if protocol is not None and (type(protocol) is not int or protocol <= 0):
        raise ValueError(f'{path}: invalid protocol')
    if version is not None and protocol is None:
        raise ValueError(f'{path}: version profile requires protocol')
    if version is not None and path.stem != f'peer-{version}':
        raise ValueError(f'{path}: version does not match profile name')
    not_applicable = _mapping(path, 'not_applicable', data.get('not_applicable', {}))
    unsupported = _mapping(path, 'unsupported', data.get('unsupported', {}))
    return Profile(path.stem, version, protocol, not_applicable, unsupported)

def load_binaries(directory):
    directory = Path(directory)
    data = json.loads((directory / 'binaries.json').read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    files = {path.name for path in directory.glob('rsync_*')}
    if not isinstance(data, dict) or not data or set(data) != files:
        raise ValueError('archive manifest does not match the peer binaries')
    fields = {'source', 'protocol', 'sha256', 'recipe', 'compiler', 'target', 'libc'}
    for name, record in data.items():
        if not re.fullmatch(r'rsync_\d+\.\d+\.\d+', name) or not isinstance(record, dict) or set(record) != fields:
            raise ValueError(f'invalid archive record: {name}')
        if type(record['protocol']) is not int or record['protocol'] <= 0:
            raise ValueError(f'invalid archive protocol: {name}')
        for field, size in (('source', 40), ('sha256', 64), ('recipe', 64)):
            if not isinstance(record[field], str) or not re.fullmatch(f'[0-9a-f]{{{size}}}', record[field]):
                raise ValueError(f'invalid archive {field}: {name}')
        if any(not isinstance(record[field], str) or not record[field].strip()
               for field in ('compiler', 'target', 'libc')):
            raise ValueError(f'invalid archive build inputs: {name}')
        path = directory / name
        if path.is_symlink() or not path.is_file() or file_digest(path) != record['sha256']:
            raise ValueError(f'archived binary differs from its manifest: {name}')
    return data

def parse_peer_banner(text):
    match = re.search(r'^rsync\s+version\s+(\d+(?:\.\d+)+)(?:-\S+)?\s+protocol\s+version\s+(\d+)\s*$',
                      text, re.MULTILINE)
    if not match:
        raise ValueError('invalid rsync version banner')
    return match.group(1), int(match.group(2))

def merge_profiles(profiles):
    version = None
    protocol = None
    not_applicable = {}
    unsupported = set()
    for profile in profiles:
        if profile.version:
            if version and version != profile.version:
                raise ValueError(f'conflicting versions: {version}, {profile.version}')
            version = profile.version
        if profile.protocol:
            if protocol and protocol != profile.protocol:
                raise ValueError(f'conflicting protocols: {protocol}, {profile.protocol}')
            protocol = profile.protocol
        for capability, reason in profile.not_applicable.items():
            not_applicable.setdefault(capability, set()).add(reason)
        unsupported.update(profile.unsupported)
    unsupported.difference_update(not_applicable)
    reasons = {capability: tuple(sorted(values))
               for capability, values in not_applicable.items()}
    return version, protocol, reasons, unsupported
