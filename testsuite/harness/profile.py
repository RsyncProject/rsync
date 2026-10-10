from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

@dataclass(frozen=True)
class Profile:
    name: str
    peer: Optional[str]
    protocol: Optional[int]
    unsupported: dict
    xfail: dict

def _mapping(path, field, values):
    if not isinstance(values, dict):
        raise ValueError(f'{path}: invalid {field}')
    if not all(isinstance(key, str) and key and isinstance(value, str) and value
               for key, value in values.items()):
        raise ValueError(f'{path}: invalid {field}')
    return dict(values)

def load_profile(path, tests):
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'{path}: profile must be an object')
    if set(data) - {'peer', 'protocol', 'unsupported', 'xfail'}:
        raise ValueError(f'{path}: unknown profile field')
    peer = data.get('peer')
    if peer is not None and (not isinstance(peer, str) or not re.fullmatch(r'\d+(?:\.\d+)+', peer)):
        raise ValueError(f'{path}: invalid peer')
    protocol = data.get('protocol')
    if protocol is not None and (type(protocol) is not int or protocol <= 0):
        raise ValueError(f'{path}: invalid protocol')
    if peer is not None and protocol is None:
        raise ValueError(f'{path}: peer profile requires protocol')
    if peer is not None and path.stem != f'peer-{peer}':
        raise ValueError(f'{path}: peer does not match profile name')
    unsupported = _mapping(path, 'unsupported', data.get('unsupported', {}))
    xfail = _mapping(path, 'xfail', data.get('xfail', {}))
    unknown = set(xfail) - set(tests)
    if unknown:
        raise ValueError(f'{path}: unknown tests: {", ".join(sorted(unknown))}')
    return Profile(path.stem, peer, protocol, unsupported, xfail)

def parse_peer_banner(text):
    match = re.search(r'^rsync\s+version\s+(\d+(?:\.\d+)+)(?:-\S+)?\s+protocol\s+version\s+(\d+)\s*$',
                      text, re.MULTILINE)
    if not match:
        raise ValueError('invalid rsync version banner')
    return match.group(1), int(match.group(2))

def merge_profiles(profiles):
    peer = None
    protocol = None
    unsupported = set()
    xfail = {}
    for profile in profiles:
        if profile.peer:
            if peer and peer != profile.peer:
                raise ValueError(f'conflicting peers: {peer}, {profile.peer}')
            peer = profile.peer
        if profile.protocol:
            if protocol and protocol != profile.protocol:
                raise ValueError(f'conflicting protocols: {protocol}, {profile.protocol}')
            protocol = profile.protocol
        unsupported.update(profile.unsupported)
        for test, reason in profile.xfail.items():
            if test in xfail and xfail[test] != reason:
                raise ValueError(f'{test}: conflicting xfail reasons')
            xfail[test] = reason
    return peer, protocol, unsupported, xfail
