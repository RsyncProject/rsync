# Testing rsync

The runner and harness use Python 3.7 or later and its standard library. Some tests also need platform tools for ACLs, extended attributes, SSH or privilege changes. CommonMark is needed to build manual pages rather than to run tests.

## Running tests

From a configured build directory:
```sh
make check
make check29
make check30
```

The first target runs the suite with the default protocol; the others force protocols 29 and 30. Make runs up to eight tests at once. Set CHECK_J to change that limit.

After building the helpers with make check-progs, individual tests and filename patterns can be selected directly:
```sh
python3 -S testsuite/runtests.py -j8 chmod-temp-dir 'xattrs*'
python3 -S testsuite/runtests.py --help
python3 -S testsuite/runtests.py --describe-tests
```

The runner defaults to serial execution. Out-of-tree builds need --srcdir, --tooldir and --rsync-bin. The installcheck target uses the installed binary with the build directory's helpers.

Scratch directories default to `testtmp/<name>/` and are retained after failures. Root and non-root runs need separate scratch directories. SIGTERM stops queued tests and terminates active test process groups; incomplete runs fail.

Daemon tests normally use pipes. The --use-tcp option uses loopback listeners, some with unauthenticated writable modules or deliberately unsafe settings. Other local users can reach those listeners. Pipe transport is preferable on shared hosts. The --daemon-tests-only option selects tests which exercise the transport.

## Tests and profiles

Tests are grouped under tests/ by build, daemon, metadata, path, protocol, rrsync, selftests and transfer. Reusable code lives in harness/ and fixture programs and data in fixtures/. Test names are lowercase, hyphen-separated filenames and must be unique across groups. Selectors and results use the filename without its `.py` ending.

Tests declare applicability with `@requires(...)` or a top-level `metadata(...)` call. Transports, protocol versions, peer-version limits and required capabilities determine selection. A min_peer value can set separate primary and secondary release limits. The --describe-tests output lists these declarations.

Regression tests should fail against the faulty implementation and check the affected behaviour directly. Fixtures must keep their data inside the test's scratch directory, tolerate parallel execution and stop their child processes. Sleeps cannot establish ordering or prove that a race was exercised.

Profiles under profiles/ distinguish tests which do not apply from missing capabilities. Root and nonroot profiles must match the effective UID. Peer profiles identify the archived release and wire protocol. Historical binaries and their build records are under [old_versions](../old_versions/README.md); --rsync-bin2 selects the secondary executable and --archived-role identifies which side runs the archived release.

The tools/check.py launcher takes a build directory, binary, profile list and fresh output directory followed by runner options. It saves run.log and receipt.json, then checks the receipt against the executable and profiles.

Result | Meaning
--- | ---
N/A | Excluded by applicability metadata
PASS | Assertions passed
FAIL | An assertion failed
ERROR | The test or harness failed unexpectedly
SKIP | Skipped without a capability
UNSUPPORTED | A required capability was unavailable
PROFILE_ERROR | A result disagreed with the active profile

With profiles active, an unsupported result is accepted only when the test declares the capability and the profile permits its absence. A generic skip fails the run. Without profiles, skips and unsupported results are accepted. Receipts record raw outcomes, profile verdicts, exclusions, executable hashes and the effective UID.

## CI

[ci.yml](../.github/workflows/ci.yml) defines the platform groups and required checks. Native jobs exercise root and non-root transfers where available. Historical-peer jobs run applicable tests in both executable roles over pipe and TCP. Diagnostics include Python 3.7, Clang 21 and sanitisers. Valgrind has a separate group. Coverage reports omit daemon work after chroot because its counter files are outside the chroot.

Ubuntu 24.04 and 26.04 exercise the older and newer xattr syscall paths on X86-64 and ARM64. One Ubuntu 22.04 job retains nested-user-namespace coverage unavailable on the newer runners. AlmaLinux 8 covers older libc and OpenSSL versions and the bundled string functions. Alpine covers musl on X86-64 and X86-32. BSD and macOS jobs use the current releases; additional versions need a distinct rsync behaviour to justify another job.

Installed-binary checks exercise restricted SSH and local TLS with certificate and hostname verification. Archive qualification builds and tests the release source archive without Git or Autoconf, then checks installation and removal. Linux storage checks use disposable filesystems and loop devices for encrypted volumes, mount boundaries, space exhaustion, device transfers and --copy-as.

Android jobs execute static Bionic binaries, then reuse the X86-64 binary in an emulator and the Termux application sandbox with SELinux enforcing. Interoperability jobs run directed transfers between Linux, FreeBSD, NetBSD, OpenBSD, macOS, Solaris and Cygwin. They compare data and metadata, check unsupported-feature refusals and restore fake-super backups on their originating platform. The separate [network document](../.github/INTEROPERABILITY.md) describes admission and cleanup.

Jobs retain logs and receipts. The Required check rejects failed or missing selected jobs.
