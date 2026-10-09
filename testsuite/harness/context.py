import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .metadata import describe


@dataclass(frozen=True)
class TestContext:
    scratch: Path
    source: Path
    destination: Path
    expected: Path
    repository: Path
    tools: Path

    @classmethod
    def from_env(cls):
        scratch = Path(os.environ['scratchdir'])
        return cls(scratch, scratch / 'from', scratch / 'to', scratch / 'chk',
                   Path(os.environ['srcdir']), Path(os.environ['TOOLDIR']))


def run(function: Callable) -> None:
    if os.environ.get('RSYNC_TEST_DESCRIBE') == '1':
        print(json.dumps(describe(function), separators=(',', ':'), sort_keys=True))
        return
    function(TestContext.from_env())
