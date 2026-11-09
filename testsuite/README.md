# rsync testsuite

Rsync's automated tests live here. Bug fixes **should** include a regression test when practical.

## Layout
- `testsuite/tests/` groups test scripts by the subsystem they exercise
- `testsuite/runtests.py` is the test runner entry point
- `testsuite/harness/` contains the runner plus shared fixtures, assertions and protocol support
- `testsuite/profiles/` records platform capabilities and peer deviations
- `testsuite/tools/` contains standalone fixture and comparison tools
- [COVERAGE.md](COVERAGE.md) records option and daemon-parameter coverage

Some tests also use C helpers built with rsync.

The test groups are build, daemon, harness, metadata, path, protocol, rrsync and transfer. The group does not form part of a test name. Selectors, profiles and receipts use the filename without a `.py` file ending. Security, compatibility and cost remain test metadata because they apply across those groups.

Test filenames describe the behaviour under test, use lowercase words separated by hyphens and remain unique across groups.

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

The native check recipe in testsuite/tools/check.sh takes a build directory, executable, profile list and fresh output directory. Remaining arguments go to the runner. The recipe saves run.log and receipt.json alongside isolated scratch data. Root and non-root runs use different output directories. Some mutation tests still require a configured source directory.

## CI

[ci.yml](../.github/workflows/ci.yml) owns events, schedules and cancellation. macOS, Cygwin, Solaris and Version mix run as direct jobs. Local composite actions hold the macOS and Cygwin steps; the remaining job families use reusable workflows. Pull requests, master pushes and manual runs select every job; scheduled runs select the relevant daily or weekly jobs.

Family | Versions and architectures
--- | ---
Ubuntu | 22.04, 24.04 and 26.04 on AMD64 and ARM64
Alpine | 3.24.4 on AMD64 and x86
FreeBSD | 14.5 and 15.1 on AMD64
NetBSD | 10.2, 11.0 and 11.1 on AMD64
OpenBSD | 7.9 and 8.0 on AMD64
macOS | 15 and 26 on AMD64 and ARM64

Each matrix entry checks its OS release and architecture and has separate artefacts. Matrix failures do not cancel the remaining entries.

Alpine uses pinned official musl images. Its AMD64 and x86 jobs build and execute the same root and non-root pipe and TCP passes as Ubuntu, including protocol 29 and 30. The x86 job also verifies a 32-bit process with 64-bit file offsets.

Each Ubuntu row checks the installed `rsync` and `rsync-ssl` against a local TLS daemon. The fixture accepts a trusted certificate, rejects an unknown CA and a hostname mismatch and checks the hostname-only and full-verification opt-outs. It does not contact a public rsync service.

Ubuntu, macOS and the BSD guests have separate root and non-root pipe and TCP passes. Each pass retains its receipt and log. macOS, FreeBSD and OpenBSD also check the built binary's required features with testsuite/tools/check-features.py, using capability and algorithm names from `rsync -VV`.

FreeBSD guests provide a bounded tmpfs through RSYNC_TEST_TMP for cross-device tests. Directory inheritance is checked against native filesystem operations. Ubuntu jobs enable fs.protected_regular before testing.

VM guest scripts record their exit status before returning control to the VM action so failed runs still copy their results back. The host checks that status after the copy completes; a missing or non-zero status fails the job.

BSD and macOS jobs retain dirfd-probe.tsv alongside their test results. `make dirfd-probe` builds a standalone tool which takes an existing writable scratch directory and refuses root. Its rows record directory-open flags, identity checks, known-file reads, creation, fchdir, search-permission revocation and O_NOFOLLOW. Operation columns use zero for success and errno values for failure; N/A marks undefined flags and unattempted operations.

The final Required check validates all selected job results and permits only the declared scheduled omissions. The Ubuntu 26.04 Clang scan runs weekly and remains non-blocking. Workflow lint also tests the result gate against failures, cancellation, missing results and unexpected skips.

## TCP daemon mode

`--use-tcp` starts unauthenticated test daemons on loopback addresses and some fixtures enable unsafe daemon options. Other local users can reach those listeners, so use the default pipe transport on shared hosts.

`--daemon-tests-only` is for a TCP pass that follows a full pipe pass. It omits tests that cannot observe the transport choice.

## Results and profiles

Label | Meaning
--- | ---
N/A | The active profile excludes the test before execution
PASS | The assertion passed
FAIL | The assertion failed
ERROR | The test environment or harness failed
SKIP | The test did not run
UNSUPPORTED | A declared capability was unavailable
XFAIL | A known defect reproduced
UNEXPECTED_PASS | A known defect no longer reproduced
PROFILE_ERROR | The result disagreed with the active profile

Exit codes are 0 for pass, 1 for fail, 2 for error, 77 for skip and 78 for expected failure.

Profiles compose by name. For example `--profiles=linux,peer-3.4.1` combines the Linux capabilities with the known deviations for that peer. Tests tagged `version-mix` are selected from metadata. Transport, privilege, instrumentation, protocol and operating-system requirements which do not apply are recorded as N/A and are not executed.

The root and nonroot profiles must match the effective UID and cannot be combined. Permission tests still check the filesystem restrictions they need.

An unsupported result is accepted only when the test declares the capability and the active profile permits its absence. A generic skip is a profile error. Expected failures require a named defect in the active profile; errors and skips do not satisfy that expectation. Receipts retain N/A selections, raw outcomes, profile verdicts, executable hashes and effective UID. Where Git can identify the source checkout, they also record its revision and whether the working tree has changes. Those fields are null when Git metadata is unavailable.

## Scratch data and requirements

Tests use `testtmp/<name>/`; failed scratch directories remain for inspection. The suite needs Python 3, `/bin/sh` and the normal build toolchain. ACL and extended-attribute tests also need the platform ACL and attr tools. Linux root permission tests require `setpriv`.

## Differential testing

The version comparison tool runs the same transfer with two rsync binaries and compares the result, diagnostics, file data, metadata and optional peak memory.

Examples:
```sh
python3 -m testsuite.tools.compare_versions
python3 -m testsuite.tools.compare_versions --sweep all -j5
python3 -m testsuite.tools.compare_versions --loop --timelimit 3600 --cost
```

The available transports are local copy, `support/lsh.sh`, a pipe daemon, a TCP daemon and rrsync. Pair each binary with the matching rrsync script when testing that transport. Historical binaries live in `old_versions/`. Investigate differences and confirm the intended behaviour before adding a focused test under the relevant `testsuite/tests/` group.
