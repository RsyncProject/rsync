# Expected-skip lists

`runtests.py` checks, on a full run, that the set of tests which skipped is
*exactly* the set that was expected. That oracle is what stops a test from
quietly turning into a permanent no-op: if a test starts skipping (a probe
regresses, a helper goes missing) the run fails instead of reporting green.

The expected set is passed in `RSYNC_EXPECT_SKIPPED`. It used to be one huge
comma-separated line per CI job, duplicated across seven workflow steps, which
meant every branch that added a skipping test edited the same line in the same
files and conflicted with every other such branch. The lists now live here, one
test name per line, and a workflow step references them:

```yaml
run: RSYNC_EXPECT_SKIPPED=@testsuite/skiplist/common.txt,@testsuite/skiplist/linux.txt make check
```

Adding a test therefore touches one line of one file, and two branches adding
different tests merge cleanly.

## Files

| file | contents |
| --- | --- |
| `common.txt` | skipped on every platform that runs the oracle — mostly `require_tcp` / `require_asan` tests, which the default stdio-pipe `make check` cannot satisfy |
| `linux.txt` | Linux-only additions |
| `macos.txt` | macOS-only additions |
| `cygwin.txt` | Cygwin-only additions |
| `proto29.txt` | additions for a `--protocol=29` run, on any platform |

Compose them with commas; the result is the union, so listing a test twice is
harmless. Plain test names may be mixed in with `@FILE` entries.

## Format rules

One test name per line. `#` starts a comment; a trailing `# reason` is
encouraged — it is the only place the reason for the expectation is recorded.
Each file must be **sorted and duplicate-free**, and every name must be a plain
test name matching a real `testsuite/<name>_test.py`. Sorting is not cosmetic:
it is what makes two independent additions land on different lines.

`runtests.py` exits 2 on anything malformed — an unreadable file, an empty or
comment-only one, an empty entry (`a,,b`, which is what an unset variable
expands to), an unsorted or duplicated name, a stale name. The set comparison
is exact, so a truncated list would not pass unnoticed; it would surface as a
page of "unexpected skips" naming every test the list lost, which reads like a
suite-wide regression rather than a bad list. Failing at the source says what
actually happened. A wholly empty `RSYNC_EXPECT_SKIPPED` is still the
legitimate "expect no skips at all".

Relative `@FILE` paths resolve against `srcdir`, so out-of-tree builds and
`make installcheck` work.

## Changing a list

If a test newly skips on a platform, prefer fixing the test so it does not skip.
When the skip is legitimate, add the name to the narrowest file that fits, with
a reason. Do not paper over a mismatch by adding a name you cannot explain — an
unexpected skip is usually a real regression in that test's setup.

`testsuite/fleettest.py` reads these same lists (through each target's
workflow), and merges per-box `expect_skip_extra` from `fleettest.json` on top
for facts that are true of one machine rather than one platform.

Fleet takes each pass's spec from the workflow step of the same name, so a
target with `"protocols": [29]` is only pinned if its workflow actually has a
`make check29` step composing `proto29.txt`. Today only the Ubuntu workflows
do; a macOS or Cygwin target set to run protocol 29 gets no expected-skip
oracle for that pass rather than a wrong one.

## `backport.txt` — a different thing in the same directory

A backport branch (`v3.4.1-sec-patches3`, `v3.2.7-sec-patches3`) is tested with
a NEWER suite than it shipped with, via
`fleettest.py --repo <backport> --testsuite-repo <3.5.0>`. Such a tree cannot
pass tests for fixes it does not carry, and cannot build unit-test helpers its
Makefile has never heard of.

Those branches each carry their own `testsuite/skiplist/backport.txt`. It is
**not** an expected-skip list:

* the other files here are `RSYNC_EXPECT_SKIPPED` oracles — "these should skip,
  tell me if that changes";
* `backport.txt` is an `RSYNC_EXCLUDE` list — "do not run these at all".

It has to be an exclusion because some of the tests *fail* rather than skip on
an older tree, and an expected-skip list cannot describe a failure.

`fleettest.py` reads it from the tree being BUILT (`--repo`), not from the tree
providing the suite, because only the built tree knows what it lacks. The
overlay that puts a newer `testsuite/` onto an older tree is a merge with no
delete, so a file that exists only on the backport survives it.

`skiplist-spec` exempts this name from the rule that every committed list must
be referenced by a workflow: nothing references it, by design.

### Accepted breakage vs absent features

`backport.txt` holds two different kinds of entry, and they must stay
distinguishable:

* **absent** — a fix, feature or helper the branch does not have. The test
  could never pass and is not telling you anything.
* **accepted breakage** — a real defect on that branch that we have chosen to
  ship. The test *was* telling you something, and excluding it is a decision
  rather than bookkeeping.

Keep the second kind in its own commented section naming the defect and where
it is written up. `v3.2.7-sec-patches3` has one today
(`fake-super-acl-xattr`, the `--remote-option` local-transfer bug). A list that
does not distinguish them turns "we know about this and accepted it" into "this
test never applied here" within about one release.

**CI for the backport branches**, when it is set up to run this suite against
them, has to honour these lists the same way `fleettest.py` does — read
`backport.txt` from the branch being built and pass it as `RSYNC_EXCLUDE`.
A CI job that does not will fail on every one of these and be turned off.
