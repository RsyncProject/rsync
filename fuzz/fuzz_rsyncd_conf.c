/*
 * Fuzz rsync's rsyncd.conf configuration parser (loadparm.c).
 *
 * Build with: make -C fuzz
 * Or via OSS-Fuzz integration.
 */
#include "rsync.h"
#include <setjmp.h>

static jmp_buf fuzz_exit_jmp;

/* Variables defined in main.c which we don't link against */
int am_receiver = 0;
int am_generator = 0;
int local_server = 0;
mode_t orig_umask = 022;
int batch_gen_fd = -1;
int sender_keeps_checksum = 0;
char *raw_argv[1] = {NULL};
int raw_argc = 0;
int cooked_argc = 0;
char **cooked_argv = NULL;
uid_t our_uid = 0;
gid_t our_gid = 0;
int daemon_connection = 0;

/* Stubs for functions defined in main.c */
void remember_children(UNUSED(int val)) {}
void start_server(UNUSED(int f_in), UNUSED(int f_out),
                  UNUSED(int argc), UNUSED(char *argv[])) {}
int client_run(UNUSED(int f_in), UNUSED(int f_out),
               UNUSED(pid_t pid), UNUSED(int argc), UNUSED(char *argv[])) { return 0; }
pid_t wait_process(UNUSED(pid_t pid), UNUSED(int *status_ptr),
                   UNUSED(int flags)) { return -1; }
int shell_exec(UNUSED(const char *cmd)) { return -1; }
void read_del_stats(UNUSED(int f)) {}
void write_del_stats(UNUSED(int f)) {}

/* Stubs for cleanup.c (we exclude it to avoid exit() calls) */
pid_t cleanup_child_pid = -1;
int cleanup_got_literal = 0;
int called_from_signal_handler = 0;
BOOL flush_ok_after_signal = False;

NORETURN void _exit_cleanup(UNUSED(int code), UNUSED(const char *file), UNUSED(int line)) {
    longjmp(fuzz_exit_jmp, 1);
}

void cleanup_disable(void) {}
void cleanup_set(UNUSED(const char *fnametmp), UNUSED(const char *fname),
                 UNUSED(struct file_struct *file), UNUSED(int fd_r), UNUSED(int fd_w)) {}
void cleanup_set_pid(UNUSED(pid_t pid)) {}
void close_all(void) {}

int lp_load(char *pszFname, int globals_only);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 1 || size > 16384)
        return 0;

    char tmpfile[] = "/tmp/fuzz_rsyncd_XXXXXX";
    int fd = mkstemp(tmpfile);
    if (fd < 0)
        return 0;

    if (write(fd, data, size) != (ssize_t)size) {
        close(fd);
        unlink(tmpfile);
        return 0;
    }
    close(fd);

    if (setjmp(fuzz_exit_jmp) != 0) {
        unlink(tmpfile);
        return 0;
    }

    lp_load(tmpfile, 0);
    lp_load(tmpfile, 1);

    unlink(tmpfile);
    return 0;
}
