#!/bin/sh

autoheader
autoconf

if [ "$1" = "--noconfigure" ]; then 
    exit 0;
fi

./configure "$@"
