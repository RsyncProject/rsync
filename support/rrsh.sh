#!/bin/sh
# Emulate an sshd forced command for the version comparison's rrsync transport.
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
