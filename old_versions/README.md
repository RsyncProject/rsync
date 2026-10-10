# Old rsync versions

These statically linked binaries support cross-version regression checks and the version-mixing workflow. Peer expectations live in `testsuite/profiles/`.

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

The archive starts at 2.6.0 because older releases require broader source changes to build with current toolchains.

## Rebuilding
```sh
./build_static.sh 3.2.7
./build_static.sh 3.0.9 v3.0.9
```

Set `RSYNC_REPO` to override the source repository. The script uses a temporary worktree, builds the requested tag, verifies the binary and removes the worktree.

Compatibility patches cover old configure inputs, K&R prototypes, current libc declarations and compiler diagnostics. OpenSSL and `_FORTIFY_SOURCE` are disabled so the archived binaries retain their release-era checksum and memory behaviour when used as peers.
