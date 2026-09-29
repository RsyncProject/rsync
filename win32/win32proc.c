/*
 * Process plumbing: CreateProcess in place of fork()+exec(), plus enough of
 * waitpid()/kill() for rsync's child bookkeeping.
 *
 * rsync only needs to spawn one kind of child on the client side -- the
 * remote shell (ssh) with its stdin/stdout wired to a pipe pair -- so that
 * is what win32_piped_child() does, replacing the fork/dup2/execvp dance in
 * pipe.c wholesale.
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

extern int blocking_io;

#define MAX_CHILDREN 16

static struct {
	pid_t  pid;
	HANDLE handle;
	int    is_thread;
} children[MAX_CHILDREN];

static void remember_child_kind(pid_t pid, HANDLE h, int is_thread)
{
	int i;

	for (i = 0; i < MAX_CHILDREN; i++) {
		if (!children[i].pid) {
			children[i].pid = pid;
			children[i].handle = h;
			children[i].is_thread = is_thread;
			return;
		}
	}
	CloseHandle(h);
}

static void remember_child(pid_t pid, HANDLE h)
{
	remember_child_kind(pid, h, 0);
}

void win32_remember_thread_child(pid_t pid, HANDLE h)
{
	remember_child_kind(pid, h, 1);
}

static HANDLE find_child(pid_t pid, int *slot)
{
	int i;

	for (i = 0; i < MAX_CHILDREN; i++) {
		if (children[i].pid == pid) {
			if (slot)
				*slot = i;
			return children[i].handle;
		}
	}
	return NULL;
}

static int under_wow64(void)
{
	BOOL wow = FALSE;

	return IsWow64Process(GetCurrentProcess(), &wow) && wow;
}

static size_t rstrip_slashes(char *s, size_t len)
{
	while (len && (s[len-1] == '\\' || s[len-1] == '/'))
		s[--len] = '\0';
	return len;
}

static int is_system32_dir(const char *dir, const char *windir, size_t wdlen)
{
	static const char sys32[] = "\\system32";
	char after;

	if (_strnicmp(dir, windir, wdlen) != 0)
		return 0;
	if (_strnicmp(dir + wdlen, sys32, sizeof sys32 - 1) != 0)
		return 0;
	after = dir[wdlen + sizeof sys32 - 1];
	return after == '\0' || after == '\\' || after == '/';
}

static int is_file(const char *path)
{
	DWORD a = GetFileAttributesA(path);

	return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static char *try_join(const char *dir, const char *name)
{
	static const char *const exts[] = { "", ".exe" };
	int has_ext = strchr(name, '.') != NULL;
	unsigned e;

	for (e = 0; e < sizeof exts / sizeof *exts; e++) {
		char *p;
		size_t need;

		if (*exts[e] && has_ext)
			continue;
		need = strlen(dir) + 1 + strlen(name) + strlen(exts[e]) + 1;
		if (!(p = (char *)malloc(need)))
			return NULL;
		snprintf(p, need, "%s\\%s%s", dir, name, exts[e]);
		if (is_file(p))
			return p;
		free(p);
	}
	return NULL;
}

static char *wow64_find_program(const char *prog)
{
	char windir[MAXPATHLEN], sysnative[MAXPATHLEN];
	size_t wdlen, snlen;
	const char *path, *p;
	int n;

	if (!prog || !*prog || !under_wow64())
		return NULL;

	wdlen = (size_t)GetWindowsDirectoryA(windir, sizeof windir);
	if (!wdlen || wdlen >= sizeof windir)
		return NULL;
	wdlen = rstrip_slashes(windir, wdlen);

	n = snprintf(sysnative, sizeof sysnative, "%s\\Sysnative", windir);
	if (n < 0 || (size_t)n >= sizeof sysnative)
		return NULL;
	snlen = (size_t)n;

	if (is_system32_dir(prog, windir, wdlen)) {
		const char *rest = prog + wdlen + sizeof "\\system32" - 1;
		size_t need = snlen + strlen(rest) + 1;
		char *full = (char *)malloc(need);

		if (!full)
			return NULL;
		snprintf(full, need, "%s%s", sysnative, rest);
		if (is_file(full))
			return full;
		free(full);
		return NULL;
	}

	if (strpbrk(prog, "\\/"))
		return NULL;

	if (!(path = getenv("PATH")))
		return NULL;

	for (p = path; *p; ) {
		const char *end = strchr(p, ';');
		size_t dlen = end ? (size_t)(end - p) : strlen(p);
		char dir[MAXPATHLEN], alt[MAXPATHLEN];

		if (dlen && dlen < sizeof dir) {
			memcpy(dir, p, dlen);
			dir[dlen] = '\0';
			dlen = rstrip_slashes(dir, dlen);
			if (is_system32_dir(dir, windir, wdlen)) {

				n = snprintf(alt, sizeof alt, "%s%s", sysnative,
					     dir + wdlen + sizeof "\\system32" - 1);
				if (n >= 0 && (size_t)n < sizeof alt) {
					char *hit = try_join(alt, prog);
					if (hit)
						return hit;
				}
			}
		}
		if (!end)
			break;
		p = end + 1;
	}
	return NULL;
}

static char *build_command_line(char **argv)
{
	size_t cap = 256, len = 0;
	char *cmd = (char *)malloc(cap);
	int i;

	if (!cmd)
		return NULL;
	cmd[0] = '\0';

	for (i = 0; argv[i]; i++) {
		const char *a = argv[i];
		int needs_quotes = !*a || strpbrk(a, " \t\n\v\"") != NULL;
		size_t need = strlen(a) * 2 + 4;
		size_t j;
		unsigned backslashes = 0;

		if (len + need + 1 > cap) {
			char *bigger;
			while (len + need + 1 > cap)
				cap *= 2;
			bigger = (char *)realloc(cmd, cap);
			if (!bigger) {
				free(cmd);
				return NULL;
			}
			cmd = bigger;
		}

		if (i)
			cmd[len++] = ' ';
		if (needs_quotes)
			cmd[len++] = '"';

		for (j = 0; a[j]; j++) {
			if (a[j] == '\\') {
				backslashes++;
				cmd[len++] = '\\';
				continue;
			}
			if (a[j] == '"') {

				for (; backslashes; backslashes--)
					cmd[len++] = '\\';
				cmd[len++] = '\\';
			}
			backslashes = 0;
			cmd[len++] = a[j];
		}

		if (needs_quotes) {

			for (; backslashes; backslashes--)
				cmd[len++] = '\\';
			cmd[len++] = '"';
		}
		cmd[len] = '\0';
	}
	return cmd;
}

static int make_pipe(HANDLE *parent_end, HANDLE *child_end, int parent_reads)
{
	SECURITY_ATTRIBUTES sa;
	HANDLE rd, wr;

	sa.nLength = sizeof sa;
	sa.lpSecurityDescriptor = NULL;
	sa.bInheritHandle = TRUE;

	if (!CreatePipe(&rd, &wr, &sa, 65536))
		return -1;

	if (parent_reads) {
		*parent_end = rd;
		*child_end = wr;
	} else {
		*parent_end = wr;
		*child_end = rd;
	}

	if (!SetHandleInformation(*parent_end, HANDLE_FLAG_INHERIT, 0)) {
		CloseHandle(rd);
		CloseHandle(wr);
		return -1;
	}
	return 0;
}

pid_t win32_piped_child(char **command, int *f_in, int *f_out)
{
	HANDLE to_child_parent, to_child_child;
	HANDLE from_child_parent, from_child_child;
	STARTUPINFOA si;
	PROCESS_INFORMATION pi;
	char *cmdline;
	int in_fd, out_fd;

	if (make_pipe(&to_child_parent, &to_child_child, 0) < 0) {
		errno = EMFILE;
		return -1;
	}
	if (make_pipe(&from_child_parent, &from_child_child, 1) < 0) {
		CloseHandle(to_child_parent);
		CloseHandle(to_child_child);
		errno = EMFILE;
		return -1;
	}

	cmdline = build_command_line(command);
	if (!cmdline) {
		CloseHandle(to_child_parent);
		CloseHandle(to_child_child);
		CloseHandle(from_child_parent);
		CloseHandle(from_child_child);
		errno = ENOMEM;
		return -1;
	}

	memset(&si, 0, sizeof si);
	si.cb = sizeof si;
	si.dwFlags = STARTF_USESTDHANDLES;
	si.hStdInput = to_child_child;
	si.hStdOutput = from_child_child;
	si.hStdError = GetStdHandle(STD_ERROR_HANDLE);

	if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi)) {
		DWORD err = GetLastError();
		char *native = NULL;

		if (err == ERROR_FILE_NOT_FOUND
		 && (native = wow64_find_program(command[0])) != NULL) {
			if (CreateProcessA(native, cmdline, NULL, NULL, TRUE, 0,
					   NULL, NULL, &si, &pi)) {
				free(native);
				goto started;
			}
			err = GetLastError();
			free(native);
		}

		free(cmdline);
		CloseHandle(to_child_parent);
		CloseHandle(to_child_child);
		CloseHandle(from_child_parent);
		CloseHandle(from_child_child);
		errno = (err == ERROR_FILE_NOT_FOUND) ? ENOENT : EIO;
		return -1;
	}
    started:
	free(cmdline);

	CloseHandle(to_child_child);
	CloseHandle(from_child_child);
	CloseHandle(pi.hThread);

	in_fd = _open_osfhandle((intptr_t)from_child_parent, _O_RDONLY | _O_BINARY);
	out_fd = _open_osfhandle((intptr_t)to_child_parent, _O_BINARY);
	if (in_fd < 0 || out_fd < 0) {
		CloseHandle(from_child_parent);
		CloseHandle(to_child_parent);
		CloseHandle(pi.hProcess);
		errno = EMFILE;
		return -1;
	}

	remember_child((pid_t)pi.dwProcessId, pi.hProcess);

	*f_in = in_fd;
	*f_out = out_fd;
	return (pid_t)pi.dwProcessId;
}

pid_t win32_waitpid(pid_t pid, int *status, int options)
{
	int slot = -1;
	HANDLE h = find_child(pid, &slot);
	DWORD rc, code = 0;

	if (!h) {
		errno = ECHILD;
		return -1;
	}

	rc = WaitForSingleObject(h, (options & WNOHANG) ? 0 : INFINITE);
	if (rc == WAIT_TIMEOUT)
		return 0;
	if (rc != WAIT_OBJECT_0) {
		errno = ECHILD;
		return -1;
	}

	if (children[slot].is_thread)
		GetExitCodeThread(h, &code);
	else
		GetExitCodeProcess(h, &code);
	CloseHandle(h);
	children[slot].pid = 0;
	children[slot].handle = NULL;
	children[slot].is_thread = 0;

	if (status)
		*status = (int)((code & 0xff) << 8);
	return pid;
}

int win32_kill(pid_t pid, int sig)
{
	int slot = -1;
	HANDLE h = find_child(pid, &slot);

	if (!h) {
		errno = ESRCH;
		return -1;
	}
	if (sig == 0)
		return 0;

	if (children[slot].is_thread)
		return 0;

	if (!TerminateProcess(h, 1)) {
		errno = EPERM;
		return -1;
	}
	return 0;
}
