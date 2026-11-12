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

The runner, tests and CI helpers use Python 3.7 or later and its standard library. CI checks Python 3.7 with the harness self-tests, CLI transfers and CI helper tests. Test subprocesses disable site packages with `-S`. Shell remains in package bootstrap, build commands and tests which exercise shell behaviour. CommonMark is a man-page build dependency. ACL and extended-attribute tests need the corresponding platform tools. Linux root permission tests require setpriv.

Scratch data defaults to `testtmp/<name>/`. Failed tests retain their scratch directories. Root and non-root runs need separate scratch directories.

During execution, SIGTERM stops queued tests and terminates active test process groups. Incomplete runs fail.

The `--use-tcp` option starts unauthenticated test daemons on loopback addresses and some fixtures enable unsafe daemon options. Other local users can reach those listeners. Use the default pipe transport on shared hosts. The `--daemon-tests-only` option limits a follow-up TCP pass to tests which can observe the transport choice.

Ubuntu also runs tools/check-ssh.py against the installed binary. It requires root and OpenSSH, creates temporary host and client keys, pins the host key and restricts commands through support/rrsync. It checks transfers, deletion, checksum updates and refusals for arbitrary commands, parent-directory access and incorrect host keys. The listener is loopback-only and all keys are removed after the check.

Historical binaries live in `old_versions/`. The runner's `--rsync-bin2` option selects the secondary binary and `--archived-role` identifies which side uses the archived release. The static Linux X86-64 peers omit OpenSSL and IDN support.

## Writing a test

Tests live under testsuite/tests in the build, daemon, metadata, path, protocol, rrsync, selftests and transfer groups. The selftests group checks the harness and its tools. Reusable support lives in testsuite/harness; fixture data and specialised fixture builders live in testsuite/fixtures. Filenames describe the behaviour, use lowercase words separated by hyphens and must be unique across groups. Selectors, profiles and receipts use the filename without its `.py` ending. [COVERAGE.md](COVERAGE.md) lists the option and daemon-parameter assertions.

Bug fixes should include a regression which fails for the original bug. Assert the affected behaviour directly; a final source and destination comparison can miss ordering, confinement or reporting failures.

Function-based tests use one `@requires(...)` decorator. Module tests use one top-level `metadata(...)` call. Declare required features, transports, protocols, peer versions, capabilities and tags. A `min_peer` value may use separate `primary` and `secondary` floors. Declare any capability passed to `test_skipped()`. Use `require_tcp()` and `require_asan()` for those requirements. Security, compatibility and cost remain metadata rather than separate test groups.

Use TestContext or the paths in harness.environment for scratch data and `write_daemon_conf()` or `build_rsyncd_conf()` for ordinary daemon configurations. Tests must tolerate parallel execution and clean up their processes, sockets and temporary files. Do not write into the source tree or use sleeps to force ordering or timestamp changes.

## Profiles and results

Profiles compose by name through `--profiles`. Platform profiles describe permitted capability gaps; peer profiles identify an archived version and protocol. The root and nonroot profiles must match the effective UID and cannot be combined. Peer runs select applicable tests from version-mix metadata.

CI invokes [tools/check.py](tools/check.py) with Python to run composed profiles. It takes the build directory, binary, profile list and a fresh output directory, followed by runner options. Each run saves run.log, receipt.json and isolated scratch data. The launcher checks the receipt's results, executable identity and profiles before returning.

Label | Meaning
--- | ---
N/A | The test does not apply to the active profile and is not executed
PASS | The assertion passed
FAIL | The assertion failed
ERROR | The test environment or harness failed
SKIP | The test skipped without identifying a missing capability
UNSUPPORTED | A declared capability was unavailable
PROFILE_ERROR | The result disagreed with the active profile

With profiles active, an unsupported result is accepted only when the test declares the capability and the profile permits its absence. A generic skip is a profile error. Failures and errors always fail the run. Without profiles, skips and unsupported results are accepted.

Individual tests use exit codes 0 for pass, 1 for failure, 2 for error and 77 for skip. Receipts retain raw outcomes, profile verdicts, N/A selections, executable hashes and the effective UID. Source revision and working-tree state are recorded when Git metadata is available.

## CI

[ci.yml](../.github/workflows/ci.yml) selects native tests, diagnostics, archive qualification and historical-peer checks for pull requests and master pushes. Scheduled runs divide daily and weekly work. The Versions group runs every archived release as both the primary and secondary executable over pipe and TCP. Valgrind runs root and non-root pipe and TCP checks in its own group. Clang 18 and Clang 21 are blocking checks.

The separate Interoperability workflow follows CI runs for PRs, pushes and schedules, including fork PRs. Trusted controller code runs from the default branch. Workers have no OIDC permission and use encrypted single-use enrolment keys. The network checks exercise quarantine, two isolated pairs, revocation and cleanup. [Deployment and recovery](../.github/INTEROPERABILITY.md) describe the required identity and repository settings.

The interoperability test qualifies a common transfer fixture through native daemon pushes and pulls. It checks file data, modes, timestamps, hard links, symlink targets, checksum updates and deletions. The shared case plan covers all 42 directed pairs between Linux, FreeBSD, NetBSD, OpenBSD, macOS, Solaris and Cygwin, both client roles and SSH and daemon transports. Connected execution of those 168 cases remains the next stage.

Environment | Versions and architectures
--- | ---
Ubuntu | 22.04, 24.04 and 26.04 on X86-64 and ARM64
AlmaLinux | 8 on X86-64
Alpine | 3.24.4 on X86-64 and X86-32
FreeBSD | 14.5 and 15.1 on X86-64
NetBSD | 10.2, 11.0 and 11.1 on X86-64
OpenBSD | 7.9 and 8.0 on X86-64
macOS | 15 and 26 on X86-64 and ARM64
Cygwin | X86-64 on Windows Server 2022
Solaris | X86-64 guest using the VM action's default release
Android | API 24 Bionic on ARM64, ARMv7 and X86-64; Termux userspace and an API 36 emulator on X86-64

Ubuntu, Alpine, macOS and BSD jobs run separate root and non-root pipe and TCP passes. Ubuntu and Alpine also force protocols 29 and 30. Alpine exercises musl and verifies 64-bit file offsets in its x86 process. Each Ubuntu row checks installed rsync and rsync-ssl against a local TLS daemon for trusted and rejected certificates, hostname mismatch and explicit verification opt-outs.

Android jobs run local transfers with static Bionic binaries. ARM64 and ARMv7 use QEMU user mode; X86-64 executes directly. The X86-64 binary is built once and its artefact and checksum are reused by the Termux container and emulator jobs. Transfers check file data, delta matching, updates, deletion, permissions, deliberately old timestamps and symlinks. The API 36 emulator uses Google APIs image revision 7 and hardware acceleration. It runs as the Android shell user with SELinux enforcing, requires hard-link creation to be denied and checks rejection of unreadable sources. Userspace jobs check successful hard-link transfers. Emulator results retain the device identity, binary hash and emulator and transfer logs. Termux application-sandbox coverage and native transfers between different operating systems remain missing.

The Archive job uses the release archive builder and generated release files. It extracts the archive, blocks Git and Autoconf tools, builds out of tree and runs pipe and TCP passes. The Git file-time tool is checked in checkout CI rather than the archive pass. Installation checks exercise rsync, rrsync and rsync-ssl, check manual pages and verify that uninstall retains unrelated files. The installed rrsync fixture sets its documented RSYNC value to the installed candidate. Results retain the archive, its hash, build logs and test receipts. Signing and publication remain separate operations.

Native passes retain logs and receipts. BSD and macOS jobs also retain dirfd-probe.tsv for directory-descriptor capability checks. The Required job rejects failed or missing selected checks. Workflow tests cover its failure and skip handling.
