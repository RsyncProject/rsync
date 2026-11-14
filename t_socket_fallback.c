/*
 * Verify socket creation on platforms without a directory-relative bind.
 *
 * This program is distributable under the terms of the GNU GPL (see COPYING).
 */

#include "rsync.h"

int dry_run = 0;
int am_root = 0;
int am_sender = 0;
int read_only = 0;
int list_only = 0;
int copy_links = 0;
int copy_unsafe_links = 0;

extern int am_daemon, am_chrooted;

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

int main(UNUSED(int argc), UNUSED(char *argv[]))
{
	char dir[] = "/tmp/rsync-socket-fallback.XXXXXX";
	STRUCT_STAT st;
	int ret = 1;

	if (!mkdtemp(dir) || chdir(dir) < 0 || mkdir("sub", 0700) < 0) {
		perror("socket test setup");
		return 2;
	}
	am_daemon = 1;
	am_chrooted = 0;

	errno = 0;
	if (do_mknod_at("sub/socket", S_IFSOCK | 0600, 0) == 0
	 || errno != EOPNOTSUPP) {
		fprintf(stderr, "nested socket creation did not fail with EOPNOTSUPP\n");
		goto out;
	}
	if (do_lstat("sub/socket", &st) == 0) {
		fprintf(stderr, "nested socket was created through a pathname bind\n");
		goto out;
	}

	if (do_mknod_at("socket", S_IFSOCK | 0600, 0) < 0) {
		fprintf(stderr, "top-level socket creation failed: %s\n", strerror(errno));
		goto out;
	}
	if (do_lstat("socket", &st) < 0 || !S_ISSOCK(st.st_mode)) {
		fprintf(stderr, "top-level socket was not created\n");
		goto out;
	}
	ret = 0;

out:
	unlink("sub/socket");
	unlink("socket");
	rmdir("sub");
	if (chdir("/") == 0)
		rmdir(dir);
	return ret;
}
