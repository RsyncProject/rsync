/* Run a testsuite script with a timeout. */

#include "rsync.h"

#define MAX_TEST_SECONDS (3*60)

 int main(int argc, char *argv[])
{
	pid_t pid;
	int status, slept = 0;

	if (argc < 2) {
		fprintf(stderr, "Usage: testrun [SHELL_OPTIONS] TESTSUITE_SCRIPT [ARGS]\n");
		exit(1);
	}

	if ((pid = fork()) < 0) {
		fprintf(stderr, "TESTRUN ERROR: fork failed: %s\n", strerror(errno));
		exit(1);
	}

	if (pid == 0) {
		argv[0] = "sh";
		execvp(argv[0], argv);
		fprintf(stderr, "TESTRUN ERROR: failed to exec %s: %s\n", argv[0], strerror(errno));
		_exit(1);
	}

	while (1) {
		int ret = waitpid(pid, &status, WNOHANG);
		if (ret > 0)
			break;
		if (ret < 0) {
			if (errno == EINTR)
				continue;
			fprintf(stderr, "TESTRUN ERROR: waitpid failed: %s\n", strerror(errno));
			exit(1);
		}
		if (slept++ > MAX_TEST_SECONDS) {
			fprintf(stderr, "TESTRUN TIMEOUT: test took over %d seconds.\n", MAX_TEST_SECONDS);
			if (kill(pid, SIGTERM) < 0)
				fprintf(stderr, "TESTRUN ERROR: failed to kill pid %ld: %s\n", (long)pid, strerror(errno));
			else
				fprintf(stderr, "TESTRUN INFO: killed pid %ld\n", (long)pid);
			exit(1);
		}
		sleep(1);
	}

	if (!WIFEXITED(status))
		exit(255);

	return WEXITSTATUS(status);
}
