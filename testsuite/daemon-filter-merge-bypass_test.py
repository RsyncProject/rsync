#!/usr/bin/env python3
# Daemon-side --filter merge-file bypasses the module's daemon_filter_list.
#
# Threat (Mitchell Benjamin report, May 2026):
#   A daemon module has `filter = - /secret/***` to hide its /secret/
#   subtree from clients.  A client that connects to that module with
#       -M --filter=._/secret/rules
#   (i.e. --remote-option=--filter=._<path>) makes the daemon's option
#   parser run parse_filter_str() on the server side.  That goes through
#   parse_merge_name() (exclude.c:599), which under sanitize_paths=True
#   prepends `module_dir` to absolute merge-file paths, then calls
#   parse_filter_file().
#
#   parse_filter_file() (exclude.c:1457) *does* check daemon_filter_list
#   before fopen(), but against the post-merge-name absolute path
#   (e.g. /srv/rsync/MOD/secret/rules) — the daemon's anchored rule
#   `- /secret/***` only matches the module-relative form `/secret/...`,
#   so the check passes and fopen() succeeds.
#
#   Compare options.c:1522-1544 (OPT_EXCLUDE_FROM / OPT_INCLUDE_FROM)
#   which strips `module_dirlen` from the front BEFORE check_filter():
#       dir = cp + (*cp == '/' ? module_dirlen : 0);
#       clean_fname(dir, CFN_COLLAPSE_DOT_DOT_DIRS);
#       rej = check_filter(&daemon_filter_list, FLOG, dir, 0) < 0;
#   The merge-file path lacks this strip.  That asymmetry is the bug.
#
# Oracle: make /secret/rules contain `- *` (exclude everything).  When the
#   daemon's sender parses it, the rule joins filter_list and excludes
#   public.txt too.  So:
#     * baseline pull (no --filter)        -> public.txt arrives.
#     * attack pull (--filter=._/secret/rules):
#         - if vulnerable: public.txt is EXCLUDED (rules file was parsed
#                          and its rule applied).
#         - if fixed:      public.txt still arrives (rules file refused
#                          before fopen()).
#
#   Negative control: --remote-option=--exclude-from=/secret/rules is
#   already rejected by the daemon (the working code path).  We don't
#   require it as a hard assertion (the daemon may reject early before we
#   can introspect), but it is documented behaviour.
#
# Suggested fix: in parse_filter_file() (exclude.c:1457), strip
# module_dirlen the same way OPT_EXCLUDE_FROM does, OR have
# parse_merge_name() expose the module-relative form for the daemon-filter
# check and use the absolute form only for fopen().

import os
import subprocess

from rsyncfns import (
    SCRATCHDIR, makepath, rmtree, rsync_argv, start_test_daemon,
    test_fail, write_daemon_conf,
)

DAEMON_PORT = 12921

# Module root and the two files: a public one (the baseline transfer
# witness) and the daemon-hidden rules file (the attack target).
mod = SCRATCHDIR / 'srvmod'
rmtree(mod)
makepath(mod / 'secret')
(mod / 'public.txt').write_text("PUBLIC\n")
# A valid rsync filter rule that, if parsed, will exclude everything ---
# including public.txt.  The point of the test is that the daemon should
# never open this file at all.
(mod / 'secret' / 'rules').write_text("- *\n")

conf = write_daemon_conf([
    ('mod', {'path': str(mod),
             'read only': 'yes',
             # Anchored module-relative rule: matches /secret/anything but
             # NOT the absolute /<module-path>/secret/anything form that
             # parse_merge_name() produces.  This is exactly the rule
             # shape Mitchell's report uses.
             'filter': '- /secret/***'}),
])
url = start_test_daemon(conf, DAEMON_PORT)


def pull(args, dest):
    rmtree(dest)
    makepath(dest)
    return subprocess.run(rsync_argv('-a', *args, f'{url}mod/', f'{dest}/'),
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True)


# -- baseline: public.txt is delivered without any client filter -----------
base_dest = SCRATCHDIR / 'base'
rc = pull([], base_dest)
if rc.returncode not in (0, 23):
    test_fail(f"baseline pull failed (rc={rc.returncode}): {rc.stderr!r}")
if not (base_dest / 'public.txt').is_file():
    test_fail("baseline: public.txt did not transfer; the module is "
              "mis-configured (daemon filter is hiding it) and the test "
              "would be vacuous.")
if (base_dest / 'secret' / 'rules').exists():
    test_fail("baseline: /secret/rules WAS delivered -- the module's "
              "daemon filter is not active and the oracle would be "
              "meaningless.")


# -- attack: --filter=._/secret/rules pushed to the daemon side via -M -----
# The daemon sender re-parses this option, runs parse_filter_str ->
# parse_merge_name -> parse_filter_file on "/secret/rules", and (when
# vulnerable) fopen()s it despite the anchored module-filter.
atk_dest = SCRATCHDIR / 'atk'
rc = pull(['-M', '--filter=._/secret/rules'], atk_dest)
# rc may legitimately be 0 (everything excluded) or 23 (partial); both
# are accepted -- the security signal is what landed in atk_dest.
if (atk_dest / 'secret' / 'rules').exists():
    test_fail("daemon delivered /secret/rules itself -- this should never "
              "happen given the daemon filter.  Test setup error?")

if not (atk_dest / 'public.txt').is_file():
    # public.txt is missing -> the hidden rule "- *" reached the daemon
    # filter list, which means parse_filter_file() did open /secret/rules.
    # That is exactly the leak we're testing for.
    test_fail(
        "daemon parsed the hidden merge-file at /secret/rules: public.txt "
        "was excluded by the rule inside that file even though the "
        "module's daemon filter (- /secret/***) should have refused the "
        "fopen().  parse_filter_file()'s daemon_filter_list check uses "
        "the post-merge-name absolute path; module_dirlen is not stripped, "
        "so an anchored module-relative rule never matches.  Fix: strip "
        "module_dirlen in exclude.c:1457 like options.c:1522 does for "
        "--exclude-from.")

# Daemon-filter-bypass not triggered: public.txt is intact.  Either the
# fix is in place, or the daemon found another way to refuse the file.
print("daemon-filter-merge-bypass: --filter merge-file refused by "
      "daemon_filter_list (public.txt survived the attack pull).")
