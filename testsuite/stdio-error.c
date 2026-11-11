#define _GNU_SOURCE
#define _FILE_OFFSET_BITS 64
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

struct reader {
	int fd;
	long left;
	FILE *stream;
	struct reader *next;
};

static struct reader *readers;
static int failed_fd = -1;

static int close_fd(int fd)
{
	int (*original)(int) = dlsym(RTLD_NEXT, "close");
	return original(fd);
}

int close(int fd)
{
	int result = close_fd(fd);
	if (fd == failed_fd) {
		failed_fd = -1;
		errno = EBADF;
	}
	return result;
}

static ssize_t read_cookie(void *cookie, char *buffer, size_t length)
{
	struct reader *reader = cookie;
	const char *mode = getenv("RSYNC_TEST_READ_ERROR");
	ssize_t result;
	if (reader->left == 0) {
		int marker = open(getenv("RSYNC_TEST_READ_MARKER"), O_WRONLY | O_CREAT | O_APPEND, 0600);
		if (marker >= 0) {
			(void)write(marker, "x", 1);
			close_fd(marker);
		}
		if (strcmp(mode, "eof") == 0)
			return 0;
		if (strcmp(mode, "eintr") == 0) {
			reader->left = -1;
			errno = EINTR;
		} else
			errno = EIO;
		return -1;
	}
	if (reader->left > 0 && length > (size_t)reader->left)
		length = reader->left;
	result = read(reader->fd, buffer, length);
	if (result > 0 && reader->left > 0)
		reader->left -= result;
	return result;
}

static int close_cookie(void *cookie)
{
	struct reader *reader = cookie, **entry = &readers;
	int result = close_fd(reader->fd);
	while (*entry && *entry != reader)
		entry = &(*entry)->next;
	if (*entry)
		*entry = reader->next;
	free(reader);
	return result;
}

FILE *fdopen(int fd, const char *open_mode)
{
	FILE *(*original)(int, const char *) = dlsym(RTLD_NEXT, "fdopen");
	const char *path = getenv("RSYNC_TEST_READ_PATH");
	const char *error_mode = getenv("RSYNC_TEST_READ_ERROR");
	struct stat actual, target;
	struct reader *reader;
	FILE *stream;
	cookie_io_functions_t functions = {.read = read_cookie, .close = close_cookie};
	if (!path || fstat(fd, &actual) < 0 || stat(path, &target) < 0
	 || actual.st_dev != target.st_dev || actual.st_ino != target.st_ino)
		return original(fd, open_mode);
	if (strcmp(error_mode, "fdopen") == 0) {
		int marker = open(getenv("RSYNC_TEST_READ_MARKER"), O_WRONLY | O_CREAT | O_APPEND, 0600);
		if (marker >= 0) {
			(void)write(marker, "x", 1);
			close_fd(marker);
		}
		failed_fd = fd;
		errno = EMFILE;
		return NULL;
	}
	reader = calloc(1, sizeof *reader);
	if (!reader)
		return NULL;
	reader->fd = fd;
	reader->left = strtol(getenv("RSYNC_TEST_READ_AFTER"), NULL, 10);
	stream = fopencookie(reader, open_mode, functions);
	if (!stream) {
		free(reader);
		return NULL;
	}
	reader->stream = stream;
	reader->next = readers;
	readers = reader;
	return stream;
}

/* Keep the backing descriptor available to early-input's fstat(). */
int fileno(FILE *stream)
{
	struct reader *reader;
	int (*original)(FILE *) = dlsym(RTLD_NEXT, "fileno");
	for (reader = readers; reader; reader = reader->next) {
		if (reader->stream == stream)
			return reader->fd;
	}
	return original(stream);
}
