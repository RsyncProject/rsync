#!/usr/bin/env python3
"""Compare the transfer performance of two rsync binaries (local <-> local).

This is a standalone dev tool (run it directly, not via runtests.py) for
spotting performance regressions between rsync releases.  Given two rsync
binaries it builds one test tree, then runs the two binaries ALTERNATELY for a
number of loops, timing each transfer, and reports the mean and standard
deviation of the transfer time for each binary.

Two transfers are timed each loop (see --mode):
  * full  -- a fresh copy into an emptied destination (end-to-end read+write).
  * noop  -- a re-run against an already-synced destination (rsync's own
             scan / file-list / stat overhead, where many regressions hide).

The first measured run of each binary is dropped (see --warmup) because it
cold-loads the source into the page cache and is an outlier.

The test tree's shape (heavy-tailed file sizes, a directory spine, symlinks,
hard links and a spread of permission modes) follows the gentestdata.py
generator; it is deterministic for a given --seed.

Examples:
    # Quick smoke run, same binary twice (means should match, no regression).
    ./perftest.py --files 200 --total-size 5M -n 3 ./rsync ./rsync

    # Compare a released binary against a fresh build over 8 loops.
    ./perftest.py -n 8 ../old_versions/rsync_3.4.0 ./rsync

    # Heavier tree, no-op (scan-overhead) timing only.
    ./perftest.py --files 50000 --total-size 2G --mode noop OLD/rsync NEW/rsync
"""

import argparse
import dataclasses
import math
import os
import random
import shlex
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time

# ---------------------------------------------------------------------------
# Test-tree generation (ported from gentestdata.py, kept self-contained).
# ---------------------------------------------------------------------------

# Marker file at the tree root; safe_rmtree only deletes a tree carrying it.
MARKER = ".perftest"

# Permission modes drawn at random for regular files (execs + read-only).
FILE_MODES = [0o644, 0o644, 0o600, 0o640, 0o664, 0o444, 0o755, 0o750, 0o700]
# Directory modes; owner always keeps r-x so the tree stays traversable.
DIR_MODES = [0o755, 0o755, 0o775, 0o750, 0o700, 0o555]

SIZE_SIGMA = 1.8          # sigma of the underlying lognormal size distribution
BASE_BUF_SIZE = 1 << 20   # 1 MiB shared random buffer for file content


def parse_size(s):
    """Parse a human size like 500M, 1.5GiB, 200KB, or a bare byte count."""
    s = s.strip()
    units = {
        "": 1, "B": 1,
        "K": 1024, "KIB": 1024, "KB": 1000,
        "M": 1024**2, "MIB": 1024**2, "MB": 1000**2,
        "G": 1024**3, "GIB": 1024**3, "GB": 1000**3,
        "T": 1024**4, "TIB": 1024**4, "TB": 1000**4,
    }
    num, suffix = s, ""
    while num and not (num[-1].isdigit() or num[-1] == "."):
        suffix = num[-1] + suffix
        num = num[:-1]
    suffix = suffix.upper()
    if suffix not in units:
        raise argparse.ArgumentTypeError(f"unknown size suffix in {s!r}")
    try:
        value = float(num)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid size {s!r}")
    return int(value * units[suffix])


def human(n):
    """Format a byte count for the summary output."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}B"
        n /= 1024


def gen_sizes(n, total, rng):
    """Return n heavy-tailed file sizes (bytes) summing to exactly `total`."""
    if n == 0:
        return []
    weights = [math.exp(rng.gauss(0.0, SIZE_SIGMA)) for _ in range(n)]
    wsum = sum(weights)
    sizes = [int(w / wsum * total) for w in weights]
    drift = total - sum(sizes)
    if drift and sizes:
        i = max(range(n), key=lambda k: sizes[k])
        sizes[i] += drift
    return sizes


def build_dirs(root, num_dirs, max_depth, rng):
    """Create `num_dirs` dirs under root, up to `max_depth` deep; return them."""
    os.makedirs(root)
    dirs = [root]
    depth_of = {root: 0}
    candidates = [root] if max_depth > 0 else []
    counter = 0

    cur = root
    for d in range(1, max_depth + 1):
        cur = os.path.join(cur, f"d{d}")
        os.mkdir(cur)
        dirs.append(cur)
        depth_of[cur] = d
        if d < max_depth:
            candidates.append(cur)

    while len(dirs) < num_dirs and candidates:
        parent = rng.choice(candidates)
        counter += 1
        child = os.path.join(parent, f"dir{counter}")
        os.mkdir(child)
        d = depth_of[parent] + 1
        dirs.append(child)
        depth_of[child] = d
        if d < max_depth:
            candidates.append(child)

    return dirs


def write_file(path, size, index, base):
    """Write a regular file of exactly `size` bytes (index/size in first 16)."""
    with open(path, "wb") as f:
        remaining = size
        if remaining >= 16:
            f.write(struct.pack("<QQ", index, size))
            remaining -= 16
        blen = len(base)
        while remaining > 0:
            chunk = base if remaining >= blen else base[:remaining]
            f.write(chunk)
            remaining -= len(chunk)


def rel_symlink(target, link_path):
    """Create a relative symlink at link_path pointing at target."""
    rel = os.path.relpath(target, os.path.dirname(link_path))
    os.symlink(rel, link_path)


def safe_rmtree(path):
    """Remove a tree, even one containing read-only directories."""
    for dirpath, _dirnames, _filenames in os.walk(path):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
    shutil.rmtree(path)


def generate_tree(root, args):
    """Build the deterministic source tree at `root`; return a summary string."""
    n = args.files
    num_dirs = args.dirs if args.dirs is not None else max(args.depth, n // 20, 1)
    n_sym = args.symlinks if args.symlinks is not None else (max(1, n // 20) if n else 0)
    n_hard = args.hardlinks if args.hardlinks is not None else (max(1, n // 20) if n else 0)

    rng = random.Random(args.seed)
    base = rng.randbytes(BASE_BUF_SIZE)

    dirs = build_dirs(root, num_dirs, args.depth, rng)
    with open(os.path.join(root, MARKER), "w") as f:
        f.write(f"generated by perftest.py seed={args.seed} files={n} "
                f"total={args.total_size}\n")

    sizes = gen_sizes(n, args.total_size, rng)
    files = []
    for i in range(n):
        path = os.path.join(rng.choice(dirs), f"file{i}.dat")
        write_file(path, sizes[i], i, base)
        files.append(path)

    hard_made = 0
    if files:
        for i in range(n_hard):
            tgt = rng.choice(files)
            link = os.path.join(rng.choice(dirs), f"hlink{i}_{os.path.basename(tgt)}")
            try:
                os.link(tgt, link)
                hard_made += 1
            except OSError:
                pass

    sym_made = 0
    for i in range(n_sym):
        link = os.path.join(rng.choice(dirs), f"sym{i}")
        roll = rng.random()
        try:
            if roll < 0.15 or not files:
                os.symlink(f"../broken-target-{i}", link)
            elif roll < 0.30:
                rel_symlink(rng.choice(dirs), link)
            else:
                rel_symlink(rng.choice(files), link)
            sym_made += 1
        except OSError:
            pass

    for path in files:
        os.chmod(path, rng.choice(FILE_MODES))
    for path in sorted((d for d in dirs if d != root),
                       key=lambda p: p.count(os.sep), reverse=True):
        os.chmod(path, rng.choice(DIR_MODES))

    return (f"files={n} dirs={len(dirs)} symlinks={sym_made} hardlinks={hard_made} "
            f"total={human(sum(sizes))} biggest={human(max(sizes) if sizes else 0)} "
            f"seed={args.seed}")


# ---------------------------------------------------------------------------
# Benchmark.
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Binary:
    label: str          # "A" / "B"
    path: str           # absolute path to the rsync binary
    version: str        # first line of `rsync --version`


def rsync_version(path):
    """Return the first line of `<rsync> --version`, or a placeholder."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
        line = (r.stdout or r.stderr or "").splitlines()
        return line[0].strip() if line else "(no --version output)"
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"(version unavailable: {e})"


def drop_caches():
    """Best-effort: flush dirty pages and drop the page/dentry/inode caches.

    Needs root to write /proc/sys/vm/drop_caches; returns True on success.
    """
    subprocess.run(["sync"], check=False)
    try:
        with open("/proc/sys/vm/drop_caches", "w") as f:
            f.write("3\n")
        return True
    except OSError:
        return False


def time_transfer(binary, rsync_args, src, dest, timeout):
    """Run one `rsync <args> src/ dest/` and return its wall-clock seconds.

    Raises RuntimeError if rsync exits non-zero (a failed transfer can't be
    timed meaningfully).
    """
    argv = [binary.path, *rsync_args, src + "/", dest + "/"]
    t0 = time.monotonic()
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    elapsed = time.monotonic() - t0
    if r.returncode != 0:
        raise RuntimeError(
            f"{binary.label} ({binary.path}) rsync exited {r.returncode}:\n"
            f"  cmd: {shlex.join(argv)}\n"
            f"  {(r.stderr or r.stdout).strip()}")
    return elapsed


def run_benchmark(binaries, args, src, dest_full, dest_noop):
    """Run the alternating loops; return {label: {mode: [all samples]}}."""
    do_full = args.mode in ("both", "full")
    do_noop = args.mode in ("both", "noop")

    # Pre-populate the shared no-op destination so every timed no-op run finds
    # nothing to do.  Use binary A; its content is identical for B.
    if do_noop:
        time_transfer(binaries[0], args.rsync_args, src, dest_noop, args.timeout)

    samples = {b.label: {m: [] for m in ("full", "noop")} for b in binaries}
    total_loops = args.warmup + args.runs

    for loop in range(total_loops):
        tag = "warmup" if loop < args.warmup else f"run {loop - args.warmup + 1}/{args.runs}"
        # Alternate which binary goes first to cancel first-mover/thermal drift.
        order = binaries if loop % 2 == 0 else list(reversed(binaries))
        for b in order:
            if do_full:
                safe_rmtree(dest_full) if os.path.exists(dest_full) else None
                os.mkdir(dest_full)
                if args.drop_caches:
                    drop_caches()
                t = time_transfer(b, args.rsync_args, src, dest_full, args.timeout)
                samples[b.label]["full"].append(t)
                _progress(b, "full", tag, t)
            if do_noop:
                if args.drop_caches:
                    drop_caches()
                t = time_transfer(b, args.rsync_args, src, dest_noop, args.timeout)
                samples[b.label]["noop"].append(t)
                _progress(b, "noop", tag, t)
    return samples


def _progress(binary, mode, tag, t):
    excl = " (warmup, excluded)" if tag == "warmup" else ""
    print(f"  [{tag:>10}] {binary.label} {mode:<4} {t:8.3f}s{excl}")


# ---------------------------------------------------------------------------
# Reporting.
# ---------------------------------------------------------------------------

def _stats(times):
    """(n, mean, stddev, min, median) over the timing samples."""
    n = len(times)
    if n == 0:
        return (0, 0.0, 0.0, 0.0, 0.0)
    return (n, statistics.mean(times),
            statistics.stdev(times) if n > 1 else 0.0,
            min(times), statistics.median(times))


def report(binaries, samples, args):
    """Print the per-binary tables and the A-vs-B comparison; return exit code."""
    print("\n" + "=" * 72)
    for b in binaries:
        print(f"{b.label}: {b.path}\n   {b.version}")
    print(f"rsync args: {' '.join(args.rsync_args)}   "
          f"(note: a full copy is not fsync'd unless you add --fsync)")
    print("=" * 72)

    modes = [m for m in ("full", "noop") if any(samples[b.label][m] for b in binaries)]
    hdr = f"{'binary':<7}{'mode':<6}{'runs':>5}{'mean':>11}{'stddev':>11}{'min':>11}{'median':>11}"

    for mode in modes:
        print(f"\n{hdr}\n{'-' * len(hdr)}")
        st = {}
        for b in binaries:
            # Drop the leading warm-up samples before computing statistics.
            kept = samples[b.label][mode][args.warmup:]
            st[b.label] = _stats(kept)
            n, mean, sd, mn, md = st[b.label]
            print(f"{b.label:<7}{mode:<6}{n:>5}{mean:>10.3f}s{sd:>10.3f}s"
                  f"{mn:>10.3f}s{md:>10.3f}s")

        a, c = binaries[0].label, binaries[1].label
        (na, ma, sda, *_), (nc, mc, sdc, *_) = st[a], st[c]
        if na and nc and ma > 0:
            delta = mc - ma
            pct = delta / ma * 100.0
            noise = max(sda, sdc)
            # Flag only when B is slower beyond the run-to-run noise and a small
            # relative threshold, so jitter doesn't cry "regression".
            if delta > noise and pct > args.threshold:
                verdict = f"REGRESSION (slower): {c} is {pct:+.1f}% vs {a}"
            elif delta < -noise and -pct > args.threshold:
                verdict = f"faster: {c} is {pct:+.1f}% vs {a}"
            else:
                verdict = f"no significant change: {pct:+.1f}% (within noise)"
            print(f"  {mode}: {a} {ma:.3f}s  vs  {c} {mc:.3f}s  ->  {verdict}")

    if args.csv:
        _write_csv(args.csv, binaries, samples)
        print(f"\nraw per-run timings written to {args.csv}")
    return 0


def _write_csv(path, binaries, samples):
    with open(path, "w") as f:
        f.write("binary,path,mode,run,warmup,seconds\n")
        for b in binaries:
            for mode in ("full", "noop"):
                for i, t in enumerate(samples[b.label][mode]):
                    f.write(f"{b.label},{b.path},{mode},{i},{int(i == 0)},{t:.6f}\n")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rsync_a", help="path to the first rsync binary (labelled A)")
    ap.add_argument("rsync_b", help="path to the second rsync binary (labelled B)")
    ap.add_argument("-n", "--runs", type=int, default=10,
                    help="measured loops per binary (default: 10)")
    ap.add_argument("--warmup", type=int, default=1,
                    help="leading runs per binary dropped from the stats to "
                         "reduce cache impact (default: 1)")
    ap.add_argument("--mode", choices=("both", "full", "noop"), default="both",
                    help="full=clean-dest copy, noop=re-sync scan overhead, "
                         "both (default)")
    ap.add_argument("--rsync-args", default="-aH",
                    help="rsync flags for the timed transfer (default: -aH)")
    ap.add_argument("--threshold", type=float, default=2.0,
                    help="percent slowdown above run-to-run noise before a "
                         "regression is flagged (default: 2.0)")
    # Tree-generation knobs (mirror gentestdata.py).
    ap.add_argument("--src", default=None,
                    help="benchmark this existing tree instead of generating one")
    ap.add_argument("-f", "--files", type=int, default=10000,
                    help="number of regular files to generate (default: 10000)")
    ap.add_argument("-s", "--total-size", type=parse_size, default="500M",
                    help="total size of all regular files (default: 500M)")
    ap.add_argument("-d", "--depth", type=int, default=10,
                    help="maximum directory tree depth (default: 10)")
    ap.add_argument("--dirs", type=int, default=None,
                    help="number of directories (default: max(depth, files/20))")
    ap.add_argument("--symlinks", type=int, default=None,
                    help="number of symlinks (default: files/20)")
    ap.add_argument("--hardlinks", type=int, default=None,
                    help="number of hard links (default: files/20)")
    ap.add_argument("--seed", type=int, default=1,
                    help="PRNG seed for a reproducible tree (default: 1)")
    ap.add_argument("--workdir", default=None,
                    help="scratch root for src/dest dirs (default: a tempdir)")
    ap.add_argument("--drop-caches", action="store_true",
                    help="sync + drop page/dentry/inode caches before each timed "
                         "run (needs root; cold-cache measurement)")
    ap.add_argument("--timeout", type=float, default=3600.0,
                    help="seconds before a single rsync run is abandoned "
                         "(default: 3600)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the scratch tree on exit (default: remove it)")
    ap.add_argument("--csv", default=None,
                    help="write raw per-run timings to this CSV file")
    args = ap.parse_args()

    if args.runs < 2:
        ap.error("--runs must be >= 2 (need >=2 samples for a stddev)")
    args.rsync_args = shlex.split(args.rsync_args)

    binaries = []
    for label, p in (("A", args.rsync_a), ("B", args.rsync_b)):
        path = os.path.abspath(p)
        if not (os.path.isfile(path) and os.access(path, os.X_OK)):
            ap.error(f"rsync {label} is not an executable file: {p}")
        binaries.append(Binary(label, path, rsync_version(path)))

    workdir = tempfile.mkdtemp(prefix="rsync-perftest-",
                               dir=args.workdir) if not args.keep or not args.workdir \
        else os.path.join(args.workdir, "rsync-perftest")
    os.makedirs(workdir, exist_ok=True)
    dest_full = os.path.join(workdir, "dest_full")
    dest_noop = os.path.join(workdir, "dest_noop")
    os.makedirs(dest_noop, exist_ok=True)

    generated = None
    if args.src:
        src = os.path.abspath(args.src)
        if not os.path.isdir(src):
            ap.error(f"--src is not a directory: {args.src}")
        print(f"using existing source tree {src}")
    else:
        src = os.path.join(workdir, "src")
        print(f"generating source tree in {src} ...")
        t0 = time.monotonic()
        summary = generate_tree(src, args)
        generated = src
        print(f"  {summary}  ({time.monotonic() - t0:.1f}s)")

    print(f"\nbenchmarking: warmup={args.warmup} runs={args.runs} mode={args.mode} "
          f"drop_caches={args.drop_caches}\n")
    rc = 1
    try:
        samples = run_benchmark(binaries, args, src, dest_full, dest_noop)
        rc = report(binaries, samples, args)
    except RuntimeError as e:
        print(f"\nbenchmark aborted: {e}", file=sys.stderr)
        rc = 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        rc = 130
    finally:
        if args.keep:
            print(f"\nkept scratch tree: {workdir}")
        else:
            for d in (dest_full, dest_noop, generated):
                if d and os.path.exists(d):
                    safe_rmtree(d)
            # Remove the workdir itself if it is now empty (i.e. we made it).
            try:
                os.rmdir(workdir)
            except OSError:
                pass
    sys.exit(rc)


if __name__ == "__main__":
    main()

# vim: sw=4 et ft=python
