import json
import os
from dataclasses import dataclass
from pathlib import Path

from .metadata import describe

@dataclass(frozen=True)
class TestContext:
    scratch: Path
    source: Path
    destination: Path
    expected: Path
    repository: Path
    tools: Path

def run(test) -> None:
    if os.environ.get('RSYNC_TEST_DESCRIBE') == '1':
        print(json.dumps(describe(test), separators=(',', ':'), sort_keys=True))
        return
    scratch = Path(os.environ['scratchdir'])
    test(TestContext(scratch, scratch / 'from', scratch / 'to', scratch / 'chk',
                     Path(os.environ['srcdir']), Path(os.environ['TOOLDIR'])))
