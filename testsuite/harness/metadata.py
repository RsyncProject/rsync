from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

@dataclass(frozen=True)
class Requirements:
    features: tuple[str, ...] = ()
    protocols: tuple[int, ...] = ()
    transports: tuple[str, ...] = ()
    min_peer: Optional[str] = None
    cost: str = 'normal'
    mutates: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self):
        groups = self.features, self.transports, self.mutates, self.tags
        if not all(isinstance(value, str) and value for values in groups for value in values):
            raise ValueError('metadata names must be non-empty strings')
        if any(type(value) is not int or value <= 0 for value in self.protocols):
            raise ValueError('protocols must be positive integers')
        if self.transports and set(self.transports) - {'pipe', 'tcp'}:
            raise ValueError('invalid transport')
        if self.min_peer is not None and (not isinstance(self.min_peer, str)
                                          or not re.fullmatch(r'\d+(?:\.\d+)+', self.min_peer)):
            raise ValueError('invalid minimum peer')
        if self.cost not in ('normal', 'expensive', 'stress'):
            raise ValueError('invalid cost')

def _ordered(values: Iterable) -> tuple:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ValueError('metadata collections must be lists, tuples or sets')
    try:
        return tuple(sorted(set(values)))
    except TypeError as error:
        raise ValueError('metadata collection values must share a type') from error

def metadata(*, features=(), protocols=(), transports=(), min_peer=None,
             cost='normal', mutates=(), tags=()):
    return Requirements(_ordered(features), _ordered(protocols), _ordered(transports), min_peer,
                        cost, _ordered(mutates), _ordered(tags))

def requires(*, features=(), protocols=(), transports=(), min_peer=None,
             cost='normal', mutates=(), tags=()):
    requirements = metadata(features=features, protocols=protocols, transports=transports,
                            min_peer=min_peer, cost=cost, mutates=mutates, tags=tags)

    def decorate(function: Callable) -> Callable:
        function.__test_requirements__ = requirements
        return function

    return decorate

def describe(function: Callable) -> dict:
    requirements = getattr(function, '__test_requirements__', Requirements())
    return asdict(requirements)

def placeholder_target(path):
    path = Path(path)
    try:
        if path.is_symlink() or path.stat().st_size > 255:
            return None
        name = path.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError):
        return None
    if not name.endswith('.py') or Path(name).name != name or name == path.name:
        return None
    target = path.with_name(name)
    return target if target.is_file() else None

def test_name(path) -> str:
    name = Path(path).name
    if not name.endswith('.py'):
        raise ValueError(f'{path}: not a test script')
    return Path(name).stem

def discover_tests(directory) -> list[Path]:
    directory = Path(directory)
    paths = sorted(path for path in directory.rglob('*.py') if not path.is_dir())
    names = {}
    for path in paths:
        name = test_name(path)
        if name in names:
            raise ValueError(f'duplicate test name {name!r}: {names[name]} and {path}')
        names[name] = path
    return [names[name] for name in sorted(names)]

def resolve_test_path(path):
    path = Path(path)
    seen = set()
    while True:
        if path in seen:
            raise ValueError(f'{path}: placeholder cycle')
        seen.add(path)
        if path.is_symlink():
            try:
                path = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError(f'{path}: invalid test link') from error
            continue
        target = placeholder_target(path)
        if not target:
            return path
        path = target

def read_requirements(path) -> Optional[dict]:
    path = resolve_test_path(path)
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    requirements = None
    for node in tree.body:
        calls = []
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            calls.append(node.value)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != 'test':
            decorators = ()
        else:
            decorators = node.decorator_list
        calls.extend(decorator for decorator in decorators if isinstance(decorator, ast.Call))
        for call in calls:
            if not isinstance(call.func, ast.Name):
                continue
            if call.func.id not in ('metadata', 'requires') or call.args:
                continue
            values = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
            requirements = metadata(**values)
            break
        if requirements:
            break

    features = set(requirements.features if requirements else ())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id == 'require_tcp':
            features.add('tcp')
        elif node.func.id == 'require_asan':
            features.add('asan')
        elif node.func.id == 'setup_chroot_inner':
            features.update(('chroot', 'root'))
        elif node.func.id == 'build_patched_rsync':
            features.add('native_build')
        elif node.func.id == 'test_skipped':
            for keyword in node.keywords:
                if keyword.arg == 'capability' and isinstance(keyword.value, ast.Constant):
                    if not isinstance(keyword.value.value, str) or not keyword.value.value:
                        raise ValueError(f'{path}: invalid capability')
                    features.add(keyword.value.value)
    if not requirements and not features:
        return None
    values = asdict(requirements or Requirements())
    values['features'] = tuple(sorted(features))
    return values

def applies_to_peer(requirements, peer, protocol, transport=None):
    if not requirements or 'version-mix' not in requirements['tags']:
        return False
    if requirements['min_peer']:
        current = tuple(int(part) for part in peer.split('.'))
        minimum = tuple(int(part) for part in requirements['min_peer'].split('.'))
        if current < minimum:
            return False
    if requirements['protocols'] and protocol not in requirements['protocols']:
        return False
    if transport and requirements['transports'] and transport not in requirements['transports']:
        return False
    return True
