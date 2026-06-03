# rsync testsuite

This directory holds rsync's automated regression tests. Ideally every code
change or bug fix comes with a test that would have caught the problem.

The tests are Python scripts named `testsuite/*_test.py`, driven by the
`runtests.py` harness at the top of the tree (the old shell-based `runtests.sh`
is gone). Shared helpers live in `testsuite/rsyncfns.py`. A handful of C helper
programs (`tls`, `getgroups`, `trimslash`, …) are built alongside `rsync` and
used by some tests. Coverage notes are in [COVERAGE.md](COVERAGE.md).

## Running the tests

### Via make

Run from the build directory:

- **`make check`** — build the helper programs and run the whole suite in
  parallel (`CHECK_J`, default 8) against the just-built `./rsync`. You do **not**
  need `make install` first; indeed you generally should not install before
  testing. Use `make check CHECK_J=1` to run serially.
- **`make check29`** / **`make check30`** — the same, forcing protocol version 29
  or 30.
- **`make installcheck`** — run the suite against the *installed* binary (e.g.
  `/usr/local/bin/rsync`). Per the GNU standards this does not search `$PATH`.
  Handy for testing a distribution build.
- **`make check-progs`** — (re)build just the C helper programs the tests need,
  without running anything.
- **`make coverage`** / **`coverage-tcp`** / **`coverage-all`** — generate an HTML
  coverage report (needs `./configure --enable-coverage` and `gcovr`);
  `coverage-all` merges runs across protocol versions and the tcp transport.

### Via runtests.py directly

`make check` just drives `runtests.py`; run it directly for finer control. It
defaults `--rsync-bin` to `./rsync`, so run it from the build directory (or pass
`--rsync-bin` / `--tooldir`):

```sh
./runtests.py                 # all tests
./runtests.py chmod-temp-dir  # a single test by name
./runtests.py 'xattr*'        # a glob of test names
```

Useful options:

- `-j N`, `--parallel N` — run up to N tests at once
- `--use-tcp` — run daemon tests against a real `rsyncd` on `127.0.0.1` (the
  default runs them over a stdio pipe). **Read the security warning below before
  using this on a shared machine.**
- `--protocol VER` — force a protocol version
- `--preserve-scratch` — keep each test's scratch dir afterwards
- `--log-level N`, `--always-log` — more verbose output / show logs for passing tests too
- `--stop-on-fail` — stop after the first failure
- `--timeout SECS` — per-test timeout (default 300)
- `--valgrind`, `--valgrind-opts OPTS` — run rsync under valgrind
- `--rsync-bin PATH`, `--tooldir DIR`, `--srcdir DIR` — locate the binary / build / source dirs
- `--expect-skipped LIST` — see skip enforcement below

### Security warning: `--use-tcp`

> **⚠️ Do not use `--use-tcp` on a machine with untrusted local users.**
>
> `--use-tcp` starts a real `rsync` daemon listening on a loopback TCP port
> (`127.0.0.1` / `::1`) and **deliberately configures insecure test scenarios**
> (daemon modules without authentication, unsafe options enabled, etc.). Loopback
> addresses are reachable by *every* local user, so for as long as the tests run,
> any other user on the machine can connect to that daemon and exploit those
> deliberately-insecure modules — potentially reading or writing files with the
> privileges of the user running the tests (which is **root** if you run the suite
> as root).
>
> Only run `--use-tcp` where there are **no possible local users who might try to
> exploit it** — a single-user workstation or a dedicated, isolated CI machine.
> The default stdio-pipe transport carries no such risk: it talks to the daemon
> over a private pipe with nothing listening on the network, so prefer it on any
> shared or multi-user host.

### Results and exit codes

Each test prints one result line — `PASS`, `FAIL`, `ERROR`, `SKIP` (with a
reason), or `XFAIL` (an expected failure) — and the run ends with a
`passed / failed / skipped` summary. Per-test exit-code convention:

| code | meaning |
|------|---------|
| 0    | pass    |
| 1    | fail    |
| 2    | error   |
| 77   | skip    |
| 78   | xfail   |

`runtests.py` exits non-zero if any test fails. Some tests need root or another
precondition and otherwise `SKIP` — read the individual test scripts for details.

**Skip enforcement:** on a full run, set `RSYNC_EXPECT_SKIPPED=a,b,c` (or
`--expect-skipped a,b,c`) and the run fails if the set of skipped tests does not
match. This is how the CI workflows pin each platform's expected skip set.

### Scratch dirs and debugging

Each test runs in `testtmp/<name>/`. On failure the scratch directory is left in
place (also `--preserve-scratch`); including its logs in a bug report is helpful.

### Preconditions

You need `python3`, `/bin/sh`, and the normal build toolchain. The ACL/xattr
tests need the `acl` and `attr` tools (`getfacl`/`setfacl`,
`getfattr`/`setfattr`) and skip if they are absent. Some tests need root.

These tests also run in CI via GitHub Actions (see `.github/workflows/`).

## Fleet testing (fleettest.py)

`testsuite/fleettest.py` builds the committed HEAD of an rsync checkout on a
fleet of remote machines over ssh and runs the suite under both transports
(stdio-pipe and `--use-tcp`) in parallel, reporting only the *unexpected*
results. It is a fast local pre-flight for the GitHub CI matrix: each target
mirrors a `.github/workflows/*.yml` job — its configure flags, and the
`RSYNC_EXPECT_SKIPPED` list parsed straight from the workflow.

Because every run includes a `--use-tcp` pass, the fleet stands up the insecure
loopback test daemon on each target — so only point it at machines with **no
untrusted local users** (see the [security warning](#security-warning---use-tcp)
above).

The fleet — which machines, and how to reach and build on each — is described in
a JSON file. Copy the bundled example (it is git-ignored) and edit it for your
hosts:

```sh
cp testsuite/fleettest.json.example testsuite/fleettest.json   # then edit
# (or symlink it, or point elsewhere with --fleet PATH)
```

Each entry names an ssh host (`null` to run locally), the workflow it mirrors,
and its configure flags, plus optional per-target settings (`make`, `privilege`,
`env_prefix`, …). See the comments in `fleettest.json.example`.

A target with `"nonroot": true` does an extra pass, after the main (root) run,
that reruns the privilege-sensitive tests as the unprivileged ssh user. Which
tests those are is **not** listed in the fleet config — a test opts in by
setting a module-level `fleet_nonroot = True`, so the set is maintained in the
test files and new privilege-sensitive tests join automatically with no
fleet-config change.

Run it from inside a checkout (it builds the current directory's HEAD; use
`--repo PATH` for another tree):

```sh
python3 testsuite/fleettest.py                       # whole fleet, both transports
python3 testsuite/fleettest.py --list                # list configured targets
python3 testsuite/fleettest.py --targets NAME[,NAME] --clean
python3 testsuite/fleettest.py --fleet other.json --transport pipe
```

Each target must be provisioned with the build toolchain its workflow installs
(autoconf, automake, a C compiler, perl, a python3 markdown module such as
cmarkgfm or commonmark unless the flags pass `--disable-md2man`, and the dev
libraries its configure flags enable). A missing piece shows up as `BUILD-FAIL`.
