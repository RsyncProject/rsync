#! /bin/sh

set -eu

if test "$#" != 3; then
	echo "usage: mixed-version.sh CURRENT PEER WORKDIR" >&2
	exit 2
fi

current=$1
peer=$2
work=$3
lsh=`dirname "$0"`/../support/lsh.sh

test ! -e "$work" || {
	echo "mixed-version work directory already exists: $work" >&2
	exit 2
}

source_dir="$work/source"
mkdir -p "$source_dir/sub"
printf 'alpha\n' >"$source_dir/file"
printf 'beta\n' >"$source_dir/sub/other"
ln "$source_dir/file" "$source_dir/hard"
ln -s sub/other "$source_dir/link"
setfattr -n user.rsync-lts -v value "$source_dir/file"
setfacl -m u:nobody:r "$source_dir/file"

verify()
{
	destination=$1
	changes=`"$current" -aHAXnci "$source_dir/" "$destination/"`
	test -z "$changes" || {
		printf '%s\n' "$changes" >&2
		return 1
	}
	test "`stat -c %i "$destination/file"`" = "`stat -c %i "$destination/hard"`"
	test "`readlink "$destination/link"`" = sub/other
	test "`getfattr --only-values -n user.rsync-lts "$destination/file" 2>/dev/null`" = value
	test "`getfacl -cp "$source_dir/file"`" = "`getfacl -cp "$destination/file"`"
}

run_case()
{
	client=$1
	server=$2
	direction=$3
	name=$4
	destination="$work/$name-$direction"
	mkdir "$destination"
	if test "$direction" = push; then
		RSYNC_RSH="$lsh" "$client" -aHAX --rsync-path="$server" \
			"$source_dir/" "localhost:$destination/"
	else
		RSYNC_RSH="$lsh" "$client" -aHAX --rsync-path="$server" \
			"localhost:$source_dir/" "$destination/"
	fi
	verify "$destination"
}

run_case "$current" "$peer" push current-client
run_case "$current" "$peer" pull current-client
run_case "$peer" "$current" push peer-client
run_case "$peer" "$current" pull peer-client
