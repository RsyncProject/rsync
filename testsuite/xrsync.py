#!/usr/bin/env python3
"""xrsync.py -- a small, runnable rsync client built on rsync_proto.py.

It speaks the rsync daemon protocol (host::module/path or rsync://host/module/
path) and supports three operations against a real rsyncd:

    xrsync.py [opts] host::module/path           # list (like rsync's listing)
    xrsync.py [opts] host::module/glob  localdir  # pull (download)
    xrsync.py [opts] localfile...       host::mod/ # push (upload, regular files)

This is deliberately a *subset* of rsync -- enough to be useful for protocol
development and as a test harness, not a drop-in replacement.  Supported flags:
-a (= -rlpt), -r, -l, -t, -p, -v, --list-only, --port=N.

Hookability: rsync_proto's DaemonClient exposes the protocol steps as small
overridable methods (recv_flist / make_request / recv_file_transfer /
make_file_token_stream / ...).  main() takes a `client_factory`, so a test can
pass a DaemonClient subclass that tampers with one step while reusing all of
xrsync's machinery.  See xrsync_test.py.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rsync_proto as rp  # noqa: E402

DEFAULT_PORT = 873
CAPS = 'e.LsfxCIu'        # the -e capability marker rsync_proto negotiates at p30


def parse_remote(spec):
    """Return (host, port, path) for a daemon spec, or None if `spec` is local.
    `path` keeps the module as its first component (what the daemon expects)."""
    if spec.startswith('rsync://'):
        rest = spec[len('rsync://'):]
        hostport, _, path = rest.partition('/')
        host, _, port = hostport.partition(':')
        return host, int(port) if port else DEFAULT_PORT, path
    if '::' in spec:
        host, _, path = spec.partition('::')
        return host, None, path
    return None


def server_opts(args, listing):
    """Build the daemon-side short-option string from the parsed flags."""
    flags = ''
    if args.recurse:
        flags += 'r'
    if args.links:
        flags += 'l'
    if args.perms:
        flags += 'p'
    if args.times:
        flags += 't'
    if listing and not args.recurse:
        flags += 'd'          # list a directory's contents without recursing
    return '-' + flags + CAPS


def do_list(host, port, module, path, args, factory):
    c = factory(host, port)
    c.handshake(module, ['--server', '--sender', server_opts(args, True),
                         '.', path], greeting_version=30)
    entries = rp.sort_entries(c.recv_flist(preserve_links=args.links))
    for e in entries:
        tgt = ''
        if e.is_link and e.link_target is not None:
            tgt = ' -> ' + e.link_target.decode('utf-8', 'surrogateescape')
        print('%s %15d %s%s' % (rp.mode_to_perms(e.mode), e.length,
                                e.name.decode('utf-8', 'surrogateescape'), tgt))
    c.finish_no_transfer()
    c.drain(timeout=1.0)
    c.close()
    return 0


def do_pull(host, port, module, path, dest, args, factory):
    c = factory(host, port)
    c.handshake(module, ['--server', '--sender', server_opts(args, False),
                         '.', path], greeting_version=30)
    c.pull(dest, verbose=args.verbose, preserve_times=args.times,
           preserve_perms=args.perms)
    c.drain(timeout=1.0)
    c.close()
    return 0


def do_push(srcs, host, port, module, path, args, factory):
    files = []
    for s in srcs:
        if os.path.isfile(s):
            with open(s, 'rb') as fh:
                files.append((os.path.basename(s), fh.read()))
        elif os.path.isdir(s):
            for root, _dirs, names in os.walk(s):
                for nm in names:
                    full = os.path.join(root, nm)
                    if os.path.isfile(full) and not os.path.islink(full):
                        rel = os.path.relpath(full, s)
                        with open(full, 'rb') as fh:
                            files.append((rel, fh.read()))
    c = factory(host, port)
    c.handshake(module, ['--server', server_opts(args, False), '.', path],
                greeting_version=30)
    c.push(files)
    if args.verbose:
        for name, _ in files:
            print(name)
    c.drain(timeout=1.0)
    c.close()
    return 0


def main(argv=None, client_factory=rp.DaemonClient):
    p = argparse.ArgumentParser(prog='xrsync.py', add_help=True)
    p.add_argument('-a', '--archive', action='store_true', help='= -rlpt')
    p.add_argument('-r', '--recursive', dest='recurse', action='store_true')
    p.add_argument('-l', '--links', action='store_true')
    p.add_argument('-p', '--perms', action='store_true')
    p.add_argument('-t', '--times', action='store_true')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('--list-only', action='store_true')
    p.add_argument('--port', type=int, default=None)
    p.add_argument('paths', nargs='+')
    args = p.parse_args(argv)
    if args.archive:
        args.recurse = args.links = args.perms = args.times = True

    paths = args.paths
    remotes = [parse_remote(x) for x in paths]

    # List: a single remote arg (or --list-only with no local dest).
    if args.list_only or (len(paths) == 1 and remotes[0] is not None):
        host, port, path = remotes[0]
        port = args.port or port or DEFAULT_PORT
        module = path.split('/')[0]
        return do_list(host, port, module, path, args, client_factory)

    if len(paths) < 2:
        p.error('need a source and a destination')
    *srcs, dest = paths
    *src_remotes, dest_remote = remotes

    if dest_remote is not None and all(r is None for r in src_remotes):
        host, port, path = dest_remote
        port = args.port or port or DEFAULT_PORT
        module = path.split('/')[0]
        return do_push(srcs, host, port, module, path, args, client_factory)

    if len(srcs) == 1 and src_remotes[0] is not None and dest_remote is None:
        host, port, path = src_remotes[0]
        port = args.port or port or DEFAULT_PORT
        module = path.split('/')[0]
        return do_pull(host, port, module, path, dest, args, client_factory)

    p.error('unsupported source/destination combination (one side must be '
            'host::module/path and the other local)')


if __name__ == '__main__':
    sys.exit(main())
