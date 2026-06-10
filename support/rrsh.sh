#!/bin/sh
# abdiff helper: a "remote shell" that emulates an sshd forced-command of
# `rrsync DIR`.  rsync invokes a remote shell as:
#     <shell-words...> [ssh-opts] <host> <rsync --server ...>
# so when used as  -e "sh rrsh.sh <RRSYNC> <DIR>"  rsync calls us as:
#     sh rrsh.sh <RRSYNC> <DIR> [opts] lh rsync --server ...
# We hand the server command to rrsync via SSH_ORIGINAL_COMMAND (exactly as
# sshd would) and exec the restricted wrapper, so abdiff can A/B the rrsync
# path itself.  Only the pretend hosts "lh"/"localhost" are accepted.
RRSYNC="$1"; DIR="$2"; shift 2
while [ $# -gt 0 ]; do
    case "$1" in
        -l) shift 2 ;;
        lh|localhost) shift; break ;;
        -*) shift ;;
        *) break ;;
    esac
done
SSH_ORIGINAL_COMMAND="$*"
export SSH_ORIGINAL_COMMAND
exec "$RRSYNC" "$DIR"
