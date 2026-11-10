import re

from harness.results import test_fail

BLOCK = 704
C_BYTE = 100

def colliding_block(offset=-1, fill=C_BYTE):
    block = bytearray([fill]) * BLOCK
    if offset >= 0:
        # This perturbation preserves both parts of the rolling checksum.
        block[offset] = fill + 1
        block[offset + 1] = fill - 2
        block[offset + 2] = fill + 1
    return bytes(block)

def spread_colliding_block(delta, fill=C_BYTE):
    block = bytearray([fill]) * BLOCK
    block[0] = fill + delta
    block[351] = fill - delta
    block[352] = fill - delta
    block[703] = fill + delta
    return bytes(block)

def transfer_stats(output):
    hits = re.search(r'hash_hits=(\d+)\s+false_alarms=(\d+)', output)
    literal = re.search(r'Literal data:\s*([\d,.]+)\s*bytes', output)
    matched = re.search(r'Matched data:\s*([\d,.]+)\s*bytes', output)
    if not hits or not literal or not matched:
        test_fail(f"could not find deltasum statistics in rsync output:\n{output}")
    number = lambda match: int(match.group(1).replace(',', '').replace('.', ''))
    return int(hits[1]), int(hits[2]), number(literal), number(matched)
