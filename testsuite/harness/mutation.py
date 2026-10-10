import os
import subprocess
import threading
import time
from pathlib import Path

from .filesystem import make_data_file, rmtree
from .process import rsync_argv
from .results import test_fail

TARGET = 'zzz_target'

def run_mutating_transfer(setup, mutate, *, options=('-a',),
                          pacer_bytes=6 * 1024 * 1024, bwlimit=1500, delay=0.6):
    base = Path(os.environ['scratchdir']) / 'mutate'
    rmtree(base)
    source = base / 'source'
    destination = base / 'destination'
    source.mkdir(parents=True)
    destination.mkdir()
    make_data_file(source / 'aaa_pacer', pacer_bytes)
    setup(source)

    errors = []

    def worker():
        time.sleep(delay)
        try:
            mutate(source)
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=worker)
    thread.start()
    result = subprocess.run(
        rsync_argv(os.environ['RSYNC'], *options, '--no-inc-recursive',
                   f'--bwlimit={bwlimit}', f'{source}/', f'{destination}/'),
        capture_output=True, text=True,
    )
    thread.join()
    if errors:
        test_fail(f'test mutation failed: {errors[0]!r}')
    return result, source, destination

def assert_no_protocol_abort(result):
    output = result.stdout + result.stderr
    if 'received more data than file length' in output:
        test_fail(f'transfer aborted after the source changed:\n{output}')
    if result.returncode == 2:
        test_fail(f'transfer ended with a protocol error:\n{output}')
    if result.returncode not in (0, 23, 24):
        test_fail(f'unexpected rsync exit {result.returncode}:\n{output}')
