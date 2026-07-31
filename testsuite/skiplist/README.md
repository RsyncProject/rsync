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
Each file must be **sorted and duplicate-free**, and every name must match a
real `testsuite/<name>_test.py`; `runtests.py` errors out otherwise. Sorting is
not cosmetic: it is what makes two independent additions land on different
lines.

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
