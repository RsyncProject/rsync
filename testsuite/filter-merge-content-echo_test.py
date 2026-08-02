#!/usr/bin/env python3
"""A merge file's contents must not come back in a filter syntax error.

A per-directory merge rule names a file the peer chooses and that travels over
the protocol, so no argument of ours ever mentions it.  Echoing an unparsable
line back made the filter parser a read-any-line oracle: every line that is not
valid filter syntax was returned verbatim to the client.  The diagnostic must
say where the bad rule is, not what it says.

Rules that came from an ARGUMENT are still echoed -- that text is the user's
own and hiding it would only make ordinary typos harder to fix.
"""

import os
import subprocess

from rsyncfns import SCRATCHDIR, makepath, rmtree, rsync_argv

SECRET = 'TOP-SECRET-PASSWORD-abc123'

base = SCRATCHDIR / 'filter-merge-content-echo'
rmtree(base)
src = base / 'src'
dest = base / 'dest'
makepath(src, dest)
(src / 'f').write_bytes(b'hi\n')

# Not valid filter syntax, so the parser reports it -- this stands in for any
# non-filter file on the server: a key, a password store, a config.
secret = base / 'secret'
secret.write_text(SECRET + '\n')

# The reachable shape: a per-dir merge file whose own contents name the target.
(src / '.rsync-filter').write_text(': ../secret\n')
got = subprocess.run(rsync_argv('-r', '-F', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode != 0, (
    f'the unparsable merge rule was accepted, so this test no longer '
    f'exercises the error path: rc={got.returncode}, out={out!r}')
assert SECRET not in out, (
    'the contents of a merged file were echoed back in the filter error, so a '
    f'peer that can choose the merge path can read any line: out={out!r}')
# The merged file's own PATH came from a rule too (the .rsync-filter names it),
# so it cannot be printed either -- but the diagnostic still has to say enough
# to be actionable.
assert 'a file read earlier' in out, (
    'the error says nothing at all about where the bad rule was, leaving no '
    f'way to act on it: out={out!r}')

# Same for a merge file reached by an explicit argument: the text is still file
# content, and --filter='. FILE' is how an operator-supplied list is read.
(base / 'rules').write_text(SECRET + '\n')
got = subprocess.run(
    rsync_argv('-r', f'--filter=. {base}/rules', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode != 0 and SECRET not in out, (
    f'a --filter=. merge file echoed its contents: rc={got.returncode}, '
    f'out={out!r}')
# This one WAS named by an argument, so its own path is the user's own text and
# is still shown -- the location must not be thrown away wholesale.
assert 'rules line 1' in out, (
    'a merge file named by an argument should still be located precisely: '
    f'out={out!r}')

# A file merged FROM a file cannot show its own path, but the place that named
# it can be shown, and is what the user needs to fix.
(base / 'outer-known').write_text(f'. {base}/inner-bad\n')
(base / 'inner-bad').write_text(SECRET + '\n')
got = subprocess.run(
    rsync_argv('-r', f'--filter=. {base}/outer-known', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert SECRET not in out and 'inner-bad' not in out, (
    f'the indirectly named merge file leaked its own path: out={out!r}')
assert 'a file named at' in out and 'outer-known line 1' in out, (
    'the error does not point at the rule that named the bad file, which is '
    f'the only thing the user can act on: out={out!r}')

# ...but a rule given AS an argument must still be shown, or an ordinary typo
# becomes undiagnosable.
got = subprocess.run(
    rsync_argv('-r', '--filter=Zbogus-rule-text', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode != 0 and 'Zbogus-rule-text' in out, (
    'a bad rule given on the command line is no longer echoed, which is a '
    f'usability regression rather than a leak: rc={got.returncode}, out={out!r}')

# An ordinary, VALID merge file must still work -- the diagnostic change must
# not have broken merge parsing itself.
(src / '.rsync-filter').write_text('- *.tmp\n')
(src / 'keep.txt').write_bytes(b'keep\n')
(src / 'drop.tmp').write_bytes(b'drop\n')
ok = subprocess.run(rsync_argv('-r', '-F', f'{src}/', f'{dest}/'),
                    capture_output=True, text=True, timeout=30)
assert ok.returncode == 0, f'a valid merge file failed: out={ok.stderr!r}'
assert (dest / 'keep.txt').exists() and not (dest / 'drop.tmp').exists(), (
    'the valid merge rule was not applied, so the parser is broken')

# A merge rule INSIDE a merge file names its own file, so the failed-open
# message echoed text that came out of the file being read.  A line of the
# target that happens to parse as a merge rule leaks through it.
(base / 'outer').write_text(f'. {base}/nested\n')
(base / 'nested').write_text(f'. {SECRET}\n')
got = subprocess.run(
    rsync_argv('-r', f'--filter=. {base}/outer', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode != 0 and SECRET not in out, (
    'a nested merge file named by file content was echoed in the failed-open '
    f'message: rc={got.returncode}, out={out!r}')

# The reported location must be the file the bad rule is actually IN.  fname
# can point into parse_merge_name()'s static buffer, which a merge rule inside
# the same file overwrites, so the error blamed the nested file instead.
(base / 'child').write_text('- harmless\n')
(base / 'parent').write_text(f'. {base}/child\nZbad-in-parent\n')
got = subprocess.run(
    rsync_argv('-r', f'--filter=. {base}/parent', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert 'parent line 2' in out, (
    'the error blames the wrong file or line for a rule that follows a nested '
    f'merge, so the location it prints cannot be trusted: out={out!r}')

# A CRLF pair is one line ending, not two.
(base / 'crlf').write_bytes(b'- harmless\r\nZbad-on-line-2\r\n')
got = subprocess.run(
    rsync_argv('-r', f'--filter=. {base}/crlf', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert 'crlf line 2' in out, f'CRLF miscounted the line: out={out!r}'

# The FILTER2 traces print rule text as well, so a word-split merge -- whose
# every word becomes its own no-prefix rule -- had its contents echoed by
# add_rule() with nothing failing to parse at all.
(src / '.rsync-filter').write_text(':w- ../secret\n')
got = subprocess.run(
    rsync_argv('-r', '-F', '--debug=FILTER2', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert SECRET not in out, (
    'the filter debug trace echoed the contents of a merged file, so the '
    f'oracle survives with --debug=FILTER2: out={out!r}')

# The MATCH traces name the pattern that acted, and a pattern read from a merge
# file is that file's text.  This one is not really debug-gated: plain -vv
# raises the filter level far enough for a sender or generator to print it, so
# a stock client reaches it with no --debug at all.
#
# The pattern is written as a glob whose LITERAL text cannot appear anywhere
# else -- the file it matches is named without the brackets -- so finding it in
# the output can only mean the rule text was echoed.
PATTERN_TEXT = 'sec[r]et-marker-9x'
MATCHED_NAME = 'secret-marker-9x'
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'keep\n')
(src / MATCHED_NAME).write_bytes(b'hidden\n')
(src / '.rsync-filter').write_text('- ' + PATTERN_TEXT + '\n')
got = subprocess.run(rsync_argv('-r', '-F', '-vv', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode == 0, f'the -vv transfer failed: out={out!r}'
assert 'because of' in out, (
    f'no match was reported, so this case exercises nothing: out={out!r}')
assert PATTERN_TEXT not in out, (
    'the match trace echoed a pattern that came from a merge file, which plain '
    f'-vv is enough to reach: out={out!r}')
assert not (dest / MATCHED_NAME).exists(), (
    'the rule did not actually hide the file, so the trace above was not the '
    'one this checks')

# A deferred per-dir merge (":" rule) is parsed long after the file that named
# it was read, so the parse-time context is gone by then and the redactions
# have to come off the RULE instead.  Two leaks lived here: an over-long
# deferred name needed no verbosity at all, and -vvv printed the name plus
# whether it opened.
rmtree(src)
makepath(src / 'sub')
(src / 'sub' / 'f').write_bytes(b'x\n')
# The name has to survive parse_filter_str's own MAXPATHLEN check and only
# overflow once the scanned directory is prepended, which is a narrow window
# whose position depends on the scratch path and the platform's MAXPATHLEN --
# so search for it rather than hard-coding a length.
maxpath = os.pathconf(str(base), 'PC_PATH_MAX')
overflowed = False
for slack in range(4, 80, 4):
    pad = maxpath - len(str(src)) - slack
    if pad < 32:
        continue
    (src / '.rsync-filter').write_text(': sub/DEFERRED-SECRET-' + 'Q' * pad + '\n')
    got = subprocess.run(rsync_argv('-r', '-F', f'{src}/', f'{dest}/'),
                         capture_output=True, text=True, timeout=30)
    out = got.stdout + got.stderr
    assert 'DEFERRED-SECRET' not in out, (
        'an over-long deferred merge name was echoed, with no verbosity '
        f'needed: out={out!r}')
    if 'merge-file name overflows' in out:
        overflowed = True
        break
assert overflowed, (
    'no length reached the deferred merge-name overflow, so that path was '
    'never exercised -- widen the search')

DEFERRED_NAME = 'named-from-f[i]le-x9'   # the brackets exist only in the file
(src / '.rsync-filter').write_text(': ' + DEFERRED_NAME + '\n')
got = subprocess.run(rsync_argv('-r', '-F', '-vvv', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert DEFERRED_NAME not in out, (
    'the deferred per-dir merge name -- which came from a file -- was printed '
    f'by the -vvv trace, along with whether it opened: out={out!r}')

# Details ABOUT a file-derived rule leak too, so they go through the same
# helper: the trailing-whitespace warning is computed from the rule's last
# byte, and would otherwise tell a peer whether a hidden rule ended in a space.
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'x\n')
(src / '.rsync-filter').write_text('- trailing-ws-probe \n')
got = subprocess.run(
    rsync_argv('-r', '-F', '--debug=FILTER1', f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert 'trailing-ws-probe' not in out and 'CAUTION' not in out, (
    'a property of a file-derived rule (its trailing whitespace) was reported, '
    f'which tells the peer about text it cannot otherwise see: out={out!r}')

# ...but for the user's OWN rule the warning is exactly what they need.
got = subprocess.run(
    rsync_argv('-r', '--debug=FILTER1', '--filter=- my-own-rule ',
               f'{src}/', f'{dest}/'),
    capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert 'CAUTION' in out and 'my-own-rule' in out, (
    'the trailing-whitespace warning was lost for an argument-supplied rule, '
    f'which is a usability regression: out={out!r}')

# An over-long ARGUMENT rule must still be reported at full length -- the
# redaction helper must not quietly truncate the user's own text.
long_arg = 'ARGLONG-' + 'Q' * (maxpath + 200)
got = subprocess.run(rsync_argv('-r', f'--filter=- {long_arg}', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert 'discarding over-long filter' in out, f'expected the over-long path: out={out!r}'
assert out.count('Q') > maxpath, (
    'the over-long argument rule was truncated more aggressively than before, '
    f'losing the user\'s own text: {out.count("Q")} Qs of {maxpath + 200}')

# The ":e" modifier synthesizes a SECOND rule -- an exclude for the merge
# file's own basename -- by hand, with no template to inherit from.  Built
# that way it carried no provenance, so once parsing finished it looked
# argument-origin and the match trace printed a merge file's text verbatim.
# Plain -vv reaches this; no --debug is involved.
#
# The pattern is a glob whose literal text cannot appear anywhere else: the
# file it matches is named without the brackets, so finding the bracketed
# form in the output can only mean the rule text was echoed.
SELF_PATTERN = 'excl-self-[x]9'
SELF_MATCHED = 'excl-self-x9'
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'keep\n')
(src / SELF_MATCHED).write_bytes(b'hidden\n')
(src / '.rsync-filter').write_text(':e ' + SELF_PATTERN + '\n')
got = subprocess.run(rsync_argv('-r', '-F', '-vv', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode == 0, f'the :e transfer failed: out={out!r}'
assert 'because of' in out, (
    f'no match was reported, so this case exercises nothing: out={out!r}')
assert SELF_PATTERN not in out, (
    'the exclude-self rule that ":e" builds by hand echoed a merge file\'s '
    f'text at plain -vv, so it carries no provenance: out={out!r}')

# A per-directory merge file's fname points INTO dirbuf, which is cut back to
# the directory before the location is recorded.  Copying it after the cut
# left the location naming the DIRECTORY, losing the filename -- no leak, but
# it breaks the "redact what, keep where" bargain the rest of this relies on.
rmtree(src)
makepath(src)
(src / 'f').write_bytes(b'x\n')
(src / '.rsync-filter').write_text('Zbad-perdir\n')
got = subprocess.run(rsync_argv('-r', '-F', f'{src}/', f'{dest}/'),
                     capture_output=True, text=True, timeout=30)
out = got.stdout + got.stderr
assert got.returncode != 0 and 'Zbad-perdir' not in out, (
    f'the per-dir rule text was echoed: rc={got.returncode}, out={out!r}')
assert '.rsync-filter line 1' in out, (
    'the location names the directory instead of the merge file, so it no '
    f'longer says which file to fix: out={out!r}')
