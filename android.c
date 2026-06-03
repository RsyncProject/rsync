/*
 * Android-specific helpers.
 *
 * openat2() usability probe
 * -------------------------
 * openat2(2) is invoked directly via syscall() because the C library lacked a
 * wrapper for it for years.  Under a seccomp filter that uses
 * SECCOMP_RET_TRAP -- as the Android application sandbox does -- a disallowed
 * syscall raises SIGSYS and *kills the process* rather than failing with
 * ENOSYS, so inspecting errno after the call is too late.  We therefore probe
 * openat2() once, behind a temporary SIGSYS handler, so a trapped syscall is
 * caught and secure_relative_open_linux() can fall back to the portable
 * per-component O_NOFOLLOW resolver instead of the whole process dying.
 *
 * This is only needed on Android, so the probe body is compiled only there.
 * __ANDROID__ is defined by Bionic's headers and reflects the *target*, not
 * the build host: it is set both for NDK cross-compiles (from a Linux/macOS
 * host) and for native Termux builds, and is unset on every other platform.
 * That makes it a reliable compile-time switch for cross builds -- there is
 * nothing to detect in configure.  Everywhere else openat2() is never
 * seccomp-trapped to SIGSYS (a missing syscall simply returns ENOSYS), so
 * openat2_usable() collapses to a constant 1 with no run-time cost.
 */

#include "rsync.h"

#if defined(__ANDROID__) && defined(HAVE_OPENAT2)

#include <setjmp.h>
#include <sys/syscall.h>
#include <linux/openat2.h>

static sigjmp_buf openat2_probe_env;

static void openat2_probe_handler(int signo)
{
	(void)signo;
	siglongjmp(openat2_probe_env, 1);
}

#endif

int openat2_usable(void)
{
#if defined(__ANDROID__) && defined(HAVE_OPENAT2)
	static int cached = -1;
	struct sigaction sa, old_sa;

	if (cached >= 0)
		return cached;

	memset(&sa, 0, sizeof sa);
	sa.sa_handler = openat2_probe_handler;
	sigemptyset(&sa.sa_mask);
	if (sigaction(SIGSYS, &sa, &old_sa) != 0)
		return cached = 0;

	if (sigsetjmp(openat2_probe_env, 1) != 0) {
		/* SIGSYS delivered: openat2 is blocked by a seccomp filter. */
		cached = 0;
	} else {
		struct open_how how;
		int fd;
		memset(&how, 0, sizeof how);
		how.flags = O_RDONLY | O_DIRECTORY;
		how.resolve = RESOLVE_BENEATH | RESOLVE_NO_MAGICLINKS;
		fd = syscall(SYS_openat2, AT_FDCWD, ".", &how, sizeof how);
		if (fd >= 0)
			close(fd);
		/* Usable only if the probe actually succeeded.  Any failure --
		 * ENOSYS (kernel < 5.6), a seccomp SECCOMP_RET_ERRNO denial
		 * (EPERM/EACCES), or EINVAL (RESOLVE_BENEATH unsupported) --
		 * means we must fall back to the portable O_NOFOLLOW walk. */
		cached = fd >= 0;
	}

	sigaction(SIGSYS, &old_sa, NULL);
	return cached;
#else
	return 1;
#endif
}
