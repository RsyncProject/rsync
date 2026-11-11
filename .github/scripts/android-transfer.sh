#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	echo "usage: $0 EXECUTOR RSYNC [HARDLINKS]" >&2
	exit 2
fi

executor=$1
binary=$2
hardlinks=${3:-allowed}
case "$hardlinks" in
	allowed|denied) ;;
	*) echo "invalid hard-link expectation: $hardlinks" >&2; exit 2 ;;
esac
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
if [ "$hardlinks" = allowed ]; then
	ln "$source/dir/data" "$source/dir/hard-link"
else
	if ln "$source/dir/data" "$source/dir/hard-link" 2> "$work/hardlinks.log"; then
		echo 'Android shell unexpectedly permits hard links' >&2
		exit 1
	fi
	grep -q 'Permission denied' "$work/hardlinks.log"
fi
ln -s data "$source/dir/symbolic-link"
printf 'remove me\n' > "$source/obsolete"

run_rsync --version | sed -n '1p'
run_rsync -aH "$source/" "$destination/"
cmp "$source/dir/data" "$destination/dir/data"
[ "$(stat -c %a "$destination/dir/data")" = 640 ]
[ "$(stat -c %Y "$source/dir/data")" = "$(stat -c %Y "$destination/dir/data")" ]
if [ "$hardlinks" = allowed ]; then
	[ "$(stat -c %i "$destination/dir/data")" = "$(stat -c %i "$destination/dir/hard-link")" ]
fi
[ "$(readlink "$destination/dir/symbolic-link")" = data ]

printf 'updated Android data\n' > "$source/dir/data"
chmod 600 "$source/dir/data"
rm "$source/obsolete"
run_rsync -aH --delete "$source/" "$destination/"
cmp "$source/dir/data" "$destination/dir/data"
[ "$(stat -c %a "$destination/dir/data")" = 600 ]
if [ "$hardlinks" = allowed ]; then
	[ "$(stat -c %i "$destination/dir/data")" = "$(stat -c %i "$destination/dir/hard-link")" ]
fi
[ "$(stat -c %Y "$source/dir/data")" = "$(stat -c %Y "$destination/dir/data")" ]
[ "$(readlink "$destination/dir/symbolic-link")" = data ]
[ ! -e "$destination/obsolete" ]

echo 'Android transfer passed'
