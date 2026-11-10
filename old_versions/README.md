These Linux AMD64 static binaries support cross-version regression checks. They retain historical defects and must only run in isolated test environments. Peer expectations live in testsuite/profiles.

Binary | Version | Protocol
--- | --- | ---
rsync_2.6.0 | 2.6.0 | 27
rsync_3.0.0 | 3.0.0 | 30
rsync_3.1.0 | 3.1.0 | 31
rsync_3.1.3 | 3.1.3 | 31
rsync_3.2.0 | 3.2.0 | 31
rsync_3.2.7 | 3.2.7 | 31
rsync_3.3.0 | 3.3.0 | 31
rsync_3.4.0 | 3.4.0 | 32
rsync_3.4.1 | 3.4.1 | 32

The archive starts at 2.6.0. The manifest records each binary's SHA-256, source commit, protocol, build-script hash, compiler, target and libc. The runner checks archived bytes and release-tag identity before executing them.

The build script takes a release version:
```sh
old_versions/build_static.sh 3.2.7
```

The script builds a temporary archive of the release tag and checks the version, ELF architecture, static linkage, local transfer and stdio-daemon transfer before replacing the binary. Set RSYNC_REPO for another source checkout or RSYNC_OUTPUT to qualify a candidate separately.

Build adjustments cover old Autoconf inputs, prototypes and libc declarations. OpenSSL and _FORTIFY_SOURCE are disabled. Updating a binary also requires updating its manifest entry and running the pipe and TCP peer checks.
