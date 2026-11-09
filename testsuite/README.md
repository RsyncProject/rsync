# Testing rsync

## Running tests

From a configured build directory:
```sh
make check
```

This builds rsync and its C test helpers, then runs up to eight tests at once. Set CHECK_J to change the parallelism. The protocol targets run the suite with a forced wire version:
```sh
make check29
make check30
```

After the helpers are built, the runner accepts individual test names and shell patterns:
```sh
./testsuite/runtests.py -j8 chmod-temp-dir 'xattr*'
./testsuite/runtests.py --help
```

Direct runner invocations are serial unless given `-j`. Out-of-tree builds use the source path to runtests.py with `--srcdir`, `--tooldir` and `--rsync-bin`. Some mutation tests require the source directory to be configured too. The installcheck target tests the installed binary.

The runner and tests use Python 3 and its standard library. Test subprocesses disable site packages with `-S`. POSIX shell handles portable build and invocation steps; rsync-ssl and checks using Bash features require Bash. CommonMark is a man-page build dependency. ACL and extended-attribute tests need the corresponding platform tools. Linux root permission tests require setpriv.

Scratch data defaults to `testtmp/<name>/`. Failed tests retain their scratch directories. Root and non-root runs need separate scratch directories.

The `--use-tcp` option starts unauthenticated test daemons on loopback addresses and some fixtures enable unsafe daemon options. Other local users can reach those listeners. Use the default pipe transport on shared hosts. The `--daemon-tests-only` option limits a follow-up TCP pass to tests which can observe the transport choice.

The manual version-comparison tool runs the same transfer with two binaries and compares data, metadata and diagnostics. Its options are available from the repository root:
```sh
python3 -m testsuite.tools.compare_versions --help
```

Historical binaries live in `old_versions/`. Differential findings need investigation before becoming regression assertions. Pair each binary with its matching rrsync script when comparing restricted-shell behaviour. Optional memory measurements use Linux procfs.

## Writing a test

Tests live under testsuite/tests in the build, daemon, harness, metadata, path, protocol, rrsync and transfer groups. Filenames describe the behaviour, use lowercase words separated by hyphens and must be unique across groups. Selectors, profiles and receipts use the filename without its `.py` ending. [COVERAGE.md](COVERAGE.md) lists the option and daemon-parameter assertions.

Bug fixes should include a regression which fails for the original bug. Assert the affected behaviour directly; a final source and destination comparison can miss ordering, confinement or reporting failures.

Function-based tests use one `@requires(...)` decorator. Module tests use one top-level `metadata(...)` call. Declare required features, transports, protocols, peer versions, capabilities and tags. Declare any capability passed to `test_skipped()`. Use `require_tcp()` and `require_asan()` for those requirements. Security, compatibility and cost remain metadata rather than separate test groups.

Use TestContext or the paths in harness.rsync for scratch data and `write_daemon_conf()` or `build_rsyncd_conf()` for ordinary daemon configurations. Tests must tolerate parallel execution and clean up their processes, sockets and temporary files. Do not write into the source tree or use sleeps to force ordering or timestamp changes.

## Profiles and results

Profiles compose by name through `--profiles`. Platform profiles describe permitted capability gaps; peer profiles record known version differences. The root and nonroot profiles must match the effective UID and cannot be combined. Peer runs select applicable tests from version-mix metadata.

CI uses [tools/check.sh](tools/check.sh) to run composed profiles. It takes the build directory, binary, profile list and a fresh output directory, followed by runner options. Each run saves run.log, receipt.json and isolated scratch data.

Label | Meaning
--- | ---
N/A | The test does not apply to the active profile and is not executed
PASS | The assertion passed
FAIL | The assertion failed
ERROR | The test environment or harness failed
SKIP | The test skipped without identifying a missing capability
UNSUPPORTED | A declared capability was unavailable
XFAIL | An expected failure reproduced
UNEXPECTED_PASS | A test expected to fail passed
PROFILE_ERROR | The result disagreed with the active profile

With profiles active, an unsupported result is accepted only when the test declares the capability and the profile permits its absence. A generic skip is a profile error. Expected failures need a compatibility classification or defect reference in the profile; errors and skips do not satisfy that expectation. An unexpected pass fails the run. Without profiles, skips and unsupported results are accepted.

Individual tests use exit codes 0 for pass, 1 for failure, 2 for error, 77 for skip and 78 for expected failure. Receipts retain raw outcomes, profile verdicts, N/A selections, executable hashes and the effective UID. Source revision and working-tree state are recorded when Git metadata is available.

## CI

[ci.yml](../.github/workflows/ci.yml) selects native tests, analysis and historical-peer checks for pull requests and master pushes. Scheduled runs divide daily and weekly work. The forward Clang check on Ubuntu 26.04 runs weekly and manually as a non-blocking check; Clang 18 remains blocking.

Environment | Versions and architectures
--- | ---
Ubuntu | 22.04, 24.04 and 26.04 on AMD64 and ARM64
AlmaLinux | 8 on AMD64
Alpine | 3.24.4 on AMD64 and x86
FreeBSD | 14.5 and 15.1 on AMD64
NetBSD | 10.2, 11.0 and 11.1 on AMD64
OpenBSD | 7.9 and 8.0 on AMD64
macOS | 15 and 26 on AMD64 and ARM64
Cygwin | AMD64 on Windows Server 2022
Solaris | AMD64 guest using the VM action's default release

Ubuntu, Alpine, macOS and BSD jobs run separate root and non-root pipe and TCP passes. Ubuntu and Alpine also force protocols 29 and 30. Alpine exercises musl and verifies 64-bit file offsets in its x86 process. Each Ubuntu row checks installed rsync and rsync-ssl against a local TLS daemon for trusted and rejected certificates, hostname mismatch and explicit verification opt-outs.

Android jobs cross-compile ARM64 and ARMv7 binaries. They do not execute the testsuite in Android or Termux. Native transfers between different operating systems and release-archive qualification remain missing.

Ubuntu, Alpine, macOS, BSD and Solaris passes retain logs and receipts. Cygwin currently retains only workflow logs. BSD and macOS jobs also retain dirfd-probe.tsv for directory-descriptor capability checks. The Required job rejects failed or missing selected checks. Workflow tests cover its failure and skip handling.
