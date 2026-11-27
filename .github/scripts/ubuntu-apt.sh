#!/bin/sh
set -eu

if ! grep -qx 'ID=ubuntu' /etc/os-release; then
    echo 'Ubuntu package setup requires an Ubuntu runner' >&2
    exit 2
fi

sources=/etc/apt/sources.list.d/ubuntu.sources
if [ ! -s "$sources" ] && grep -qx 'VERSION_ID="22.04"' /etc/os-release; then
    sources=/etc/apt/sources.list
fi
if [ ! -s "$sources" ]; then
    echo 'Ubuntu archive sources are missing' >&2
    exit 2
fi

# Use the runner's signed Ubuntu archives for both update and install.
exec sudo apt-get -o "Dir::Etc::SourceList=$sources" -o Dir::Etc::SourceParts=- \
    -o APT::Get::List-Cleanup=false -o APT::Update::Error-Mode=any "$@"
