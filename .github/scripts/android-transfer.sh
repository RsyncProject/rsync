#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
	echo "usage: $0 EXECUTOR RSYNC" >&2
	exit 2
fi

executor=$1
binary=$2
[ -x "$binary" ] || { echo "not an executable file: $binary" >&2; exit 2; }
if [ "$executor" != direct ]; then
	command -v "$executor" >/dev/null
fi

run_rsync()
{
	if [ "$executor" = direct ]; then
		"$binary" "$@"
	else
		"$executor" "$binary" "$@"
	fi
}

work=$(mktemp -d "${TMPDIR:-/tmp}/rsync-android.XXXXXX")
cleanup()
{
	case "$work" in
		"${TMPDIR:-/tmp}"/rsync-android.*) rm -rf -- "$work" ;;
		*) echo "refusing to remove unexpected path: $work" >&2 ;;
	esac
}
trap cleanup 0 1 2 15
source=$work/source
destination=$work/destination
mkdir -p "$source/dir" "$destination"
printf 'initial data\n' > "$source/dir/data"
chmod 640 "$source/dir/data"
ln "$source/dir/data" "$source/dir/hard-link"
ln -s data "$source/dir/symbolic-link"
printf 'remove me\n' > "$source/obsolete"

run_rsync --version | sed -n '1p'
run_rsync -aH "$source/" "$destination/"
cmp "$source/dir/data" "$destination/dir/data"
[ "$(stat -c %a "$destination/dir/data")" = 640 ]
[ "$(stat -c %Y "$source/dir/data")" = "$(stat -c %Y "$destination/dir/data")" ]
[ "$(stat -c %i "$destination/dir/data")" = "$(stat -c %i "$destination/dir/hard-link")" ]
[ "$(readlink "$destination/dir/symbolic-link")" = data ]

printf 'updated Android data\n' > "$source/dir/data"
chmod 600 "$source/dir/data"
rm "$source/obsolete"
run_rsync -aH --delete "$source/" "$destination/"
cmp "$source/dir/data" "$destination/dir/data"
[ "$(stat -c %a "$destination/dir/data")" = 600 ]
[ "$(stat -c %i "$destination/dir/data")" = "$(stat -c %i "$destination/dir/hard-link")" ]
[ ! -e "$destination/obsolete" ]

echo 'Android transfer passed'
