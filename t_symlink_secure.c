/*
 * Test harness for the fake-super branches of do_symlink_at()/do_mknod_at().
 * Fake-super stores a symlink/device as a placeholder file, so the create
 * resolves the final component; the no-slash branch used to fall back to
 * do_symlink()/do_mknod(), whose plain open() followed a planted basename
 * symlink and escaped the module. Checks the fixed wrappers refuse it;
 * --poc shows the old fallback escaping. Not linked into rsync. GPL version 2.
 */

#include "rsync.h"

#include <sys/stat.h>

/* The symlink placeholder (and thus this escape) exists only where symlink
 * xattrs are unavailable -- the same guard do_symlink() uses. Elsewhere
 * symlink() fails EEXIST on a planted link, so only the device path applies. */
#if defined SUPPORT_LINKS && (defined NO_SYMLINK_XATTRS || defined NO_SYMLINK_USER_XATTRS)
#define TEST_SYMLINK_PLACEHOLDER 1
#endif

int dry_run = 0;
int am_root = -1;	/* --fake-super */
int am_sender = 0;
int read_only = 0;
int list_only = 0;
int copy_links = 0;
int copy_unsafe_links = 0;
extern int am_daemon, am_chrooted;

short info_levels[COUNT_INFO], debug_levels[COUNT_DEBUG];

static int errs = 0;

static void check_preserved(const char *label, const char *victim, const char *want)
{
	char buf[256];
	int fd = open(victim, O_RDONLY);
	ssize_t n = fd >= 0 ? read(fd, buf, sizeof buf - 1) : -1;

	if (fd >= 0)
		close(fd);
	if (n < 0)
		n = 0;
	buf[n] = '\0';
	if (n > 0 && buf[n-1] == '\n')
		buf[n-1] = '\0';

	if (strcmp(buf, want) != 0) {
		fprintf(stderr, "FAIL [%s]: victim %s = \"%s\", expected \"%s\" "
			"(basename symlink was followed -> module escape)\n",
			label, victim, buf, want);
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: victim %s preserved\n", label, victim);
}

static void check_clobbered(const char *label, const char *victim, const char *unwanted)
{
	char buf[256];
	int fd = open(victim, O_RDONLY);
	ssize_t n = fd >= 0 ? read(fd, buf, sizeof buf - 1) : -1;

	if (fd >= 0)
		close(fd);
	if (n < 0)
		n = 0;
	buf[n] = '\0';
	if (n > 0 && buf[n-1] == '\n')
		buf[n-1] = '\0';

	if (strcmp(buf, unwanted) != 0) {
		fprintf(stderr, "FAIL [%s]: victim %s = \"%s\", expected the escape to write \"%s\"\n",
			label, victim, buf, unwanted);
		errs++;
		return;
	}
	fprintf(stderr, "OK   [%s]: victim %s clobbered as expected (escape demonstrated)\n",
		label, victim);
}

int main(int argc, char **argv)
{
#ifndef AT_FDCWD
	fprintf(stderr, "SKIP: AT_FDCWD not available\n");
	return 77;
#else
	int poc = 0;
	const char *moddir;

# if !defined(HAVE_MKNODAT) && !defined(TEST_SYMLINK_PLACEHOLDER)
	/* Nothing left to assert: the do_mknod_at() checks need mknodat(), and
	 * the do_symlink_at() ones are not compiled here.  Skip rather than
	 * pass vacuously. */
	(void)argc; (void)argv;
	fprintf(stderr, "SKIP: no mknodat() and no symlink placeholders -- "
		"nothing this helper asserts applies to this build\n");
	return 77;
# endif

	if (argc == 3 && strcmp(argv[1], "--poc") == 0) {
		poc = 1;
		moddir = argv[2];
	} else if (argc == 2) {
		moddir = argv[1];
	} else {
		fprintf(stderr, "usage: %s [--poc] <module-dir>\n", argv[0]);
		return 2;
	}

	if (chdir(moddir) < 0) {
		perror("chdir");
		return 2;
	}

	am_daemon = 1;
	am_chrooted = 0;
	am_root = -1;	/* fake-super: symlinks/devices stored as files */

	if (poc) {
		/* Pre-fix fallback: a no-slash path went to do_symlink()/do_mknod(),
		 * which open() the basename without O_NOFOLLOW. */
#ifdef TEST_SYMLINK_PLACEHOLDER
		do_symlink("VULN_SYM_PAYLOAD", "sympath");
		check_clobbered("poc do_symlink bare", "../outside/secret_sym",
				"VULN_SYM_PAYLOAD");
#endif
		do_mknod("nodpath", S_IFCHR | 0600, 0);
		check_clobbered("poc do_mknod bare", "../outside/secret_nod", "");
		return errs ? 1 : 0;
	}

	/* Fixed wrappers: a bare-path basename symlink must not be followed;
	 * the victim outside the module stays untouched. */
#ifdef TEST_SYMLINK_PLACEHOLDER
	do_symlink_at("FIXED_SYM_PAYLOAD", "sympath");
	check_preserved("do_symlink_at bare", "../outside/secret_sym", "VICTIM_SYM");

	/* Slashed path for parity (already protected before the fix). */
	do_symlink_at("FIXED_SYM_PAYLOAD", "sub/sympath2");
	check_preserved("do_symlink_at slashed", "../outside/secret_sym2", "VICTIM_SYM2");
#endif

# ifdef HAVE_MKNODAT
	/* Without mknodat() do_mknod_at() IS do_mknod(): the confinement is
	 * compiled out by design (SECURITY.md), so these would assert a
	 * property the build deliberately does not have.  The do_symlink_at()
	 * checks above do not depend on it and still run. */
	do_mknod_at("nodpath", S_IFCHR | 0600, 0);
	check_preserved("do_mknod_at bare", "../outside/secret_nod", "VICTIM_NOD");

	do_mknod_at("sub/nodpath2", S_IFCHR | 0600, 0);
	check_preserved("do_mknod_at slashed", "../outside/secret_nod2", "VICTIM_NOD2");
# endif

	if (errs)
		fprintf(stderr, "%d failure(s)\n", errs);
	return errs ? 1 : 0;
#endif
}
