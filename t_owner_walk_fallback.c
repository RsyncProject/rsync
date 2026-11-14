/*
 * Verify the operator-path fallback used when secure directory-relative
 * traversal is unavailable.
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
extern int insecure_links;
extern int am_daemon, am_chrooted;

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

#define EXPECT_ENOSYS(expr, operation) \
	do { \
		errno = 0; \
		if ((expr) != -1 || errno != ENOSYS) { \
			fprintf(stderr, "%s did not fail with ENOSYS\n", operation); \
			goto out; \
		} \
	} while (0)

int main(UNUSED(int argc), UNUSED(char *argv[]))
{
	char root[] = "/tmp/rsync-owner-walk.XXXXXX";
	char path[MAXPATHLEN], linkpath[MAXPATHLEN], renamed[MAXPATHLEN];
	char newdir[MAXPATHLEN], nodpath[MAXPATHLEN], existingdir[MAXPATHLEN];
	char tempfile[MAXPATHLEN];
	STRUCT_STAT st;
	int fd, ret = 1;

	if (!mkdtemp(root)
	 || snprintf(path, sizeof path, "%s/file", root) >= (int)sizeof path
	 || snprintf(linkpath, sizeof linkpath, "%s/link", root) >= (int)sizeof linkpath
	 || snprintf(renamed, sizeof renamed, "%s/renamed", root) >= (int)sizeof renamed
	 || snprintf(newdir, sizeof newdir, "%s/newdir", root) >= (int)sizeof newdir
	 || snprintf(nodpath, sizeof nodpath, "%s/node", root) >= (int)sizeof nodpath
	 || snprintf(existingdir, sizeof existingdir, "%s/existing", root) >= (int)sizeof existingdir
	 || snprintf(tempfile, sizeof tempfile, "%s/temp.XXXXXX", root) >= (int)sizeof tempfile) {
		perror("test setup");
		return 2;
	}
	fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
	if (fd < 0 || close(fd) < 0 || mkdir(existingdir, 0700) < 0) {
		perror("test setup");
		goto out;
	}

	EXPECT_ENOSYS(open_no_attacker_symlinks(path, O_RDONLY, 0), "secure open");

	am_daemon = 1;
	am_chrooted = 0;
	EXPECT_ENOSYS(do_open_at(path, O_RDONLY, 0), "secure transfer open");
	EXPECT_ENOSYS(do_open_checklinks(path), "secure checked open");
	EXPECT_ENOSYS(secure_mkstemp(tempfile, 0600, 0), "secure temporary file");
	EXPECT_ENOSYS(do_stat_at(path, &st), "secure stat");
	EXPECT_ENOSYS(do_lstat_at(path, &st), "secure lstat");
	EXPECT_ENOSYS(do_chmod_at(path, 0600), "secure chmod");
	EXPECT_ENOSYS(do_lchown_at(path, (uid_t)-1, (gid_t)-1), "secure lchown");
	EXPECT_ENOSYS(do_link_at(path, linkpath), "secure link");
	EXPECT_ENOSYS(do_rename_at(path, renamed), "secure rename");
	EXPECT_ENOSYS(do_symlink_at("target", linkpath), "secure symlink");
	EXPECT_ENOSYS(do_mkdir_at(newdir, 0700), "secure mkdir");
	EXPECT_ENOSYS(do_mknod_at(nodpath, S_IFIFO | 0600, 0), "secure mknod");
	EXPECT_ENOSYS(do_unlink_at(path), "secure unlink");
	EXPECT_ENOSYS(do_rmdir_at(existingdir), "secure rmdir");
	if (access(path, F_OK) != 0 || access(existingdir, F_OK) != 0
	 || lstat(linkpath, &st) == 0 || lstat(renamed, &st) == 0
	 || lstat(newdir, &st) == 0 || lstat(nodpath, &st) == 0
	 || lstat(tempfile, &st) == 0) {
		fprintf(stderr, "a refused operation changed the filesystem\n");
		goto out;
	}

	am_daemon = 0;
	insecure_links = 1;
	fd = open_no_attacker_symlinks(path, O_RDONLY, 0);
	if (fd < 0) {
		fprintf(stderr, "explicit insecure open failed: %s\n", strerror(errno));
		goto out;
	}
	close(fd);
	if (do_unlink_at(path) < 0) {
		fprintf(stderr, "explicit insecure unlink failed: %s\n", strerror(errno));
		goto out;
	}
	ret = 0;

out:
	unlink(path);
	unlink(linkpath);
	unlink(renamed);
	unlink(nodpath);
	unlink(tempfile);
	rmdir(newdir);
	rmdir(existingdir);
	rmdir(root);
	return ret;
}
