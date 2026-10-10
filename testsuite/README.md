# rsync testsuite

Rsync's automated tests live here. Bug fixes should include a regression test when practical.

## Layout
- `testsuite/tests/` groups test scripts by the subsystem they exercise
- `testsuite/runtests.py` is the test runner entry point
- `testsuite/harness/` contains the runner plus shared fixtures, assertions and protocol support
- `testsuite/profiles/` records platform capabilities and peer deviations
- `testsuite/tools/` contains standalone fixture and comparison tools
- [COVERAGE.md](COVERAGE.md) records option and daemon-parameter coverage

Some tests also use C helpers built with rsync.

The test groups are build, daemon, harness, metadata, path, protocol, rrsync and transfer. The group does not form part of a test name. Selectors, profiles and receipts use the filename without a `.py` file ending. Security, compatibility and cost remain test metadata because they apply across those groups.

## Writing tests

A regression test should assert the behaviour being fixed. A final source and destination comparison can miss the actual bug.

Function-based tests use one `@requires(...)` decorator. Module tests use one top-level `metadata(...)` call. Declare any capability passed to `test_skipped()` and use `require_tcp()` or `require_asan()` for those checks.

Use `TestContext` or the paths in `harness.rsync` for scratch data. Do not write into the source tree or use sleeps for synchronisation and timestamp changes. Tests run in parallel by default and must clean up their processes, sockets and temporary files. Use `write_daemon_conf()` or `build_rsyncd_conf()` for ordinary daemon configurations.

## Running tests

Run the standard suite from a configured build directory:
```sh
make check
```

Other make targets:
```sh
make check CHECK_J=1
make check29
make check30
make check-progs
make installcheck
make coverage-all
```

The runner accepts names and shell patterns:
```sh
./testsuite/runtests.py
./testsuite/runtests.py chmod-temp-dir
./testsuite/runtests.py 'xattr*'
```

The main controls are `-j`, `--rsync-bin`, `--rsync-bin2`, `--protocol`, `--profiles`, `--use-tcp`, `--daemon-tests-only`, `--race-timeout`, `--receipt`, `--describe-tests` and `--valgrind`. Run `./testsuite/runtests.py --help` for the full list.

## TCP daemon mode

`--use-tcp` starts unauthenticated test daemons on loopback addresses and some fixtures enable unsafe daemon options. Other local users can reach those listeners, so use the default pipe transport on shared hosts.

`--daemon-tests-only` is for a TCP pass that follows a full pipe pass. It omits tests that cannot observe the transport choice.

## Results and profiles

Result | Meaning
--- | ---
PASS | The assertion passed
FAIL | The assertion failed
ERROR | The test environment or harness failed
SKIP | The test did not run
UNSUPPORTED | A declared capability was unavailable
XFAIL | A known defect reproduced
XPASS | A known defect no longer reproduced
PROFILE_ERROR | The result disagreed with the active profile

Exit codes are 0 for pass, 1 for fail, 2 for error, 77 for skip and 78 for expected failure.

Profiles compose by name. For example `--profiles=linux,peer-3.4.1` combines the Linux capabilities with the known deviations for that peer. Tests tagged `version-mix` are selected from metadata.

An unsupported result is accepted only when the test declares the capability and the active profile permits its absence. A generic skip is a profile error. Receipts retain the raw outcome and the profile verdict.

## Scratch data and requirements

Tests use `testtmp/<name>/`; failed scratch directories remain for inspection. The suite needs Python 3, `/bin/sh` and the normal build toolchain. ACL and extended-attribute tests also need the platform ACL and attr tools.

## Differential testing

The version comparison tool runs the same transfer with two rsync binaries and compares the result, diagnostics, file data, metadata and optional peak memory.

Examples:
```sh
python3 -m testsuite.tools.compare_versions
python3 -m testsuite.tools.compare_versions --sweep all -j5
python3 -m testsuite.tools.compare_versions --loop --timelimit 3600 --cost
```

The available transports are local copy, `support/lsh.sh`, a pipe daemon, a TCP daemon and rrsync. Pair each binary with the matching rrsync script when testing that transport.

Historical binaries live in `old_versions/`. Investigate differences and confirm the intended behaviour before adding a focused test under the relevant `testsuite/tests/` group.
