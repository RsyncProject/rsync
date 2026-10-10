# rsync testsuite

Rsync's automated tests live here. Bug fixes should include a regression test when practical.

## Layout
- `testsuite/tests/` groups test scripts by the subsystem they exercise. Test names end in `_test.py`
- `testsuite/runtests.py` discovers and runs tests
- `testsuite/rsyncfns.py` keeps legacy helper imports working during migration
- `testsuite/harness/` contains filesystem, metadata, profile, result and receipt support
- `testsuite/profiles/` records platform capabilities and peer deviations
- `testsuite/fleettest.py` runs the suite across the maintainer fleet
- `testsuite/abdiff.py` compares two rsync versions over the same transfers
- [COVERAGE.md](COVERAGE.md) records option and daemon-parameter coverage

Some tests also use C helpers built with rsync.

The test groups are `build`, `daemon`, `harness`, `metadata`, `path`, `protocol`, `rrsync` and `transfer`. The group does not form part of a test name. Selectors, profiles and receipts continue to use the filename without `_test.py`. Security, compatibility and cost remain test metadata because they apply across those groups.

## Writing tests

A regression test should assert the behaviour being fixed. A final source and destination comparison can miss the actual bug.

Function-based tests use one `@requires(...)` decorator. Existing module tests use one top-level `metadata(...)` call while they are migrated. Declare any capability passed to `test_skipped()` and use `require_tcp()` or `require_asan()` for those checks.

Use `TestContext` or the existing `rsyncfns.py` paths for scratch data. Do not write into the source tree or use sleeps for synchronisation and timestamp changes. Tests run in parallel by default and must clean up their processes, sockets and temporary files. Use `write_daemon_conf()` or `build_rsyncd_conf()` for ordinary daemon configurations.

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

## Fleet testing

`testsuite/fleettest.py` builds the committed revision on configured hosts and runs the same pipe, TCP, protocol and non-root lanes used by CI. Profiled passes validate their receipts. A target mapped to a workflow matrix declares its selected profiles in the fleet configuration. Configuration is read from `~/.fleettest.json` then `testsuite/fleettest.json` or from the path passed to `--fleet`.

Start with the checked-in example:
```sh
cp testsuite/fleettest.json.example testsuite/fleettest.json
```

Common commands:
```sh
python3 testsuite/fleettest.py
python3 testsuite/fleettest.py --list
python3 testsuite/fleettest.py --targets freebsd,netbsd
python3 testsuite/fleettest.py --keep-on-fail
```

Target-specific skip allowances require a matching unsupported capability. Non-root and protocol passes are declared in the target entry. Do not point TCP fleet runs at shared hosts.

## Differential testing

`testsuite/abdiff.py` runs the same transfer with two rsync binaries and compares the result, diagnostics, file data, metadata and optional peak memory.

Examples:
```sh
testsuite/abdiff.py
testsuite/abdiff.py --sweep all -j5
testsuite/abdiff.py --loop --timelimit 3600 --cost
```

The available transports are local copy, `support/lsh.sh`, a pipe daemon, a TCP daemon and rrsync. Pair each binary with the matching rrsync script when testing that transport.

Historical binaries live in `old_versions/` -- Investigate differences and confirm the intended behaviour before adding a focused test under the relevant `testsuite/tests/` group
