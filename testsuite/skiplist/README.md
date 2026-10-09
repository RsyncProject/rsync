# Transitional skip lists

Profiles decide whether an `UNSUPPORTED` result is valid. The files in this directory remain as a temporary exact-name comparison while every CI lane moves to profiles. `skiplist-spec` checks that both policies describe the same tests.

New policy belongs in test metadata and profiles. Update these lists only to keep the transitional comparison aligned.

## Files

File | Purpose
--- | ---
common.txt | Tests unsupported in every default pipe lane
linux.txt | Linux additions
macos.txt | macOS additions
cygwin.txt | Cygwin additions
proto29.txt | Protocol 29 additions
proto30.txt | Protocol 30 additions

CI passes one or more files through `RSYNC_EXPECT_SKIPPED`:
```yaml
run: RSYNC_EXPECT_SKIPPED=@testsuite/skiplist/common.txt,@testsuite/skiplist/linux.txt make check
```

Comma-separated files and plain test names form one union. A `-name` entry removes a test after every addition has been expanded.

## Format

Each non-comment line contains one test name. A `# reason` comment records why the temporary entry exists. The executable capability remains in the test and its permitted absence remains in the profile.

Lists must be non-empty, sorted and free of duplicates. Every entry must match `testsuite/tests/<name>_test.py`. The parser rejects unreadable files, empty entries, invalid names, stale names and removals which match nothing.

Relative `@FILE` paths resolve against `srcdir` so out-of-tree builds and `make installcheck` use the source lists.

## Updating policy

Fix an avoidable skip rather than recording it. When a platform genuinely lacks a capability:

1. Declare the capability in the test.
2. Add it to the narrowest applicable profile.
3. Add the test name to the matching transitional list.
4. Run `skiplist-spec` and the affected platform lane.

Do not use a broad capability to permit an unrelated skip.

`fleettest.py` reads the list from each target's workflow. A machine-specific `expect_skip_extra` entry also needs an `unsupported_extra` capability declared by the affected test.

Protocol passes use the policy from the matching workflow step. A target is not given a protocol skip oracle when its workflow has no corresponding protocol step.

## Backport exclusions

`backport.txt` has a different contract. It is read from the branch being built and passed as `RSYNC_EXCLUDE` when a newer testsuite runs against that branch. Excluded tests do not run at all.

Use this file only when the older branch lacks a fix, feature or helper required by the newer suite or when a documented defect has been accepted for that branch. Keep accepted defects in a separate commented section with a reference to the decision.

The overlay used by fleettest merges the newer `testsuite/` into the older tree without deleting branch files. This keeps the built branch's `backport.txt` available to the run.

`skiplist-spec` does not require `backport.txt` to appear in a workflow because it belongs to the built branch rather than the normal CI skip policy.
