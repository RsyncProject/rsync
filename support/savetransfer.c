/* This program can record the stream of data flowing to or from a program.
 * This allows it to be used to check that rsync's data that is flowing
 * through a remote shell is not being corrupted (for example).
 *
 * Usage: savetransfer [-i|-o] OUTPUT_FILE PROGRAM [ARGS...]
 * -i  Save the input going to PROGRAM to the OUTPUT_FILE
 * -o  Save the output coming from PROGRAM to the OUTPUT_FILE
 *
 * If you want to capture the flow of data for an rsync command, use one of
 * the following commands (the resulting files should be identical):
 *
 * rsync -av --rsh="savetransfer -i /tmp/to.server ssh"
 *   --rsync-path="savetransfer -i /tmp/from.client rsync" SOURCE DEST
 *
 * rsync -av --rsh="savetransfer -o /tmp/from.server ssh"
 *   --rsync-path="savetransfer -o /tmp/to.client rsync" SOURCE DEST
 *
 * Note that this program aborts after 30 seconds of inactivity, so you'll need
 * to change it if that is not enough dead time for your transfer.  Also, some
 * of the above commands will not notice that the transfer is done (if we're
 * saving the input to a PROGRAM and the PROGRAM goes away:  we won't notice
 * that it's gone unless more data comes in) -- when this happens it will delay
 * at the end of the transfer until the timeout period expires.
 */

#include "../rsync.h"

#define TIMEOUT_SECONDS 30

#ifdef HAVE_SIGACTION
static struct sigaction sigact;
#endif

void run_program(char **command);

char buf[4096];
int save_data_from_program = 0;

int
main(int argc, char *argv[])
{
    int fd_file, len;
    struct timeval tv;
    fd_set fds;

    argv++;
    if (--argc && argv[0][0] == '-') {
	if (argv[0][1] == 'o')
	    save_data_from_program = 1;
	else if (argv[0][1] == 'i')
	    save_data_from_program = 0;
	else {
	    fprintf(stderr, "Unknown option: %s\n", argv[0]);
	    exit(1);
	}
	argv++;
	argc--;
    }
    if (argc < 2) {
	fprintf(stderr, "Usage: savetransfer [-i|-o] OUTPUT_FILE PROGRAM [ARGS...]\n");
	fprintf(stderr, "-i  Save the input going to PROGRAM to the OUTPUT_FILE\n");
	fprintf(stderr, "-o  Save the output coming from PROGRAM to the OUTPUT_FILE\n");
	exit(1);
    }
    if ((fd_file = open(*argv, O_WRONLY|O_TRUNC|O_CREAT|O_BINARY, 0644)) < 0) {
	fprintf(stderr, "Unable to write to `%s': %s\n", *argv, strerror(errno));
	exit(1);
    }
    set_blocking(fd_file);

    SIGACTION(SIGPIPE, SIG_IGN);

    run_program(argv + 1);

#if defined HAVE_SETMODE && O_BINARY
    setmode(STDIN_FILENO, O_BINARY);
    setmode(STDOUT_FILENO, O_BINARY);
#endif
    set_nonblocking(STDIN_FILENO);
    set_blocking(STDOUT_FILENO);

    while (1) {
	FD_ZERO(&fds);
	FD_SET(STDIN_FILENO, &fds);
	tv.tv_sec = TIMEOUT_SECONDS;
	tv.tv_usec = 0;
	if (!select(STDIN_FILENO+1, &fds, NULL, NULL, &tv))
	    break;
	if (!FD_ISSET(STDIN_FILENO, &fds))
	    break;
	if ((len = read(STDIN_FILENO, buf, sizeof buf)) <= 0)
	    break;
	if (write(STDOUT_FILENO, buf, len) != len) {
	    fprintf(stderr, "Failed to write data to stdout: %s\n", strerror(errno));
	    exit(1);
	}
	if (write(fd_file, buf, len) != len) {
	    fprintf(stderr, "Failed to write data to fd_file: %s\n", strerror(errno));
	    exit(1);
	}
    }
    return 0;
}

void
run_program(char **command)
{
    int pipe_fds[2], ret;
    pid_t pid;

    if (pipe(pipe_fds) < 0) {
	fprintf(stderr, "pipe failed: %s\n", strerror(errno));
	exit(1);
    }

    if ((pid = fork()) < 0) {
	fprintf(stderr, "fork failed: %s\n", strerror(errno));
	exit(1);
    }

    if (pid == 0) {
	if (save_data_from_program)
	    ret = dup2(pipe_fds[1], STDOUT_FILENO);
	else
	    ret = dup2(pipe_fds[0], STDIN_FILENO);
	if (ret < 0) {
	    fprintf(stderr, "Failed to dup (in child): %s\n", strerror(errno));
	    exit(1);
	}
	close(pipe_fds[0]);
	close(pipe_fds[1]);
	set_blocking(STDIN_FILENO);
	set_blocking(STDOUT_FILENO);
	execvp(command[0], command);
	fprintf(stderr, "Failed to exec %s: %s\n", command[0], strerror(errno));
	exit(1);
    }

    if (save_data_from_program)
	ret = dup2(pipe_fds[0], STDIN_FILENO);
    else
	ret = dup2(pipe_fds[1], STDOUT_FILENO);
    if (ret < 0) {
	fprintf(stderr, "Failed to dup (in parent): %s\n", strerror(errno));
	exit(1);
    }
    close(pipe_fds[0]);
    close(pipe_fds[1]);
}

void
set_nonblocking(int fd)
{
    int val;

    if ((val = fcntl(fd, F_GETFL, 0)) == -1)
	return;
    if (!(val & NONBLOCK_FLAG)) {
	val |= NONBLOCK_FLAG;
	fcntl(fd, F_SETFL, val);
    }
}

void
set_blocking(int fd)
{
    int val;

    if ((val = fcntl(fd, F_GETFL, 0)) < 0)
	return;
    if (val & NONBLOCK_FLAG) {
	val &= ~NONBLOCK_FLAG;
	fcntl(fd, F_SETFL, val);
    }
}
