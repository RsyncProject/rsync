The rsync_* files are static Linux X86-64 binaries for mixed-version tests. We run them only in isolated test environments because they contain historical defects.

binaries.json records each binary's SHA-256, release commit, protocol and build inputs. Peer profiles under testsuite/profiles identify the releases; test metadata determines which checks apply. The runner checks binary hashes, version banners and release tags before using an archived peer.

To rebuild a release:
```sh
old_versions/build_static.sh 3.2.7
```

The script builds from the release tag and checks architecture, static linkage, local copying and a stdio-daemon transfer before replacing the binary. RSYNC_REPO selects another source checkout and RSYNC_OUTPUT selects a separate output file.

Build adjustments accommodate older Autoconf inputs and libc declarations. OpenSSL, IDN and _FORTIFY_SOURCE are disabled. When replacing a binary, update its manifest entry and run the tests in both executable roles over pipe and TCP.
