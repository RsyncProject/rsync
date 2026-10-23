#include "rsync.h"

#ifdef HAVE_SYS_RESOURCE_H
#include <sys/resource.h>
#endif
#ifdef HAVE_LINUX_IOPRIO_H
#include <linux/ioprio.h>
#endif
#ifdef HAVE_SYS_SYSCALL_H
#include <sys/syscall.h>
#endif

extern int am_server;
extern int am_daemon;

#define PRIORITY_UNSET (-100)
#define DEFAULT_NICE 19

#if defined HAVE_GETPRIORITY && defined HAVE_SYS_RESOURCE_H
#define SUPPORT_GET_PRIORITY 1
#endif

#if defined SUPPORT_GET_PRIORITY && defined HAVE_SETPRIORITY
#define SUPPORT_NICE_PRIORITY 1
#endif

#if defined HAVE_LINUX_IOPRIO_H && defined HAVE_SYS_SYSCALL_H \
 && defined SUPPORT_GET_PRIORITY \
 && defined SYS_ioprio_get && defined SYS_ioprio_set \
 && defined IOPRIO_PRIO_CLASS \
 && defined IOPRIO_PRIO_DATA && defined IOPRIO_PRIO_VALUE
#define SUPPORT_IO_PRIORITY 1
#endif

#ifdef SUPPORT_IO_PRIORITY
#ifdef IOPRIO_PRIO_LEVEL
#define RSYNC_IOPRIO_LEVEL(value) IOPRIO_PRIO_LEVEL(value)
#else
/* Older headers expose the whole data field; the level is its low 3 bits. */
#define RSYNC_IOPRIO_LEVEL(value) (IOPRIO_PRIO_DATA(value) & 7)
#endif
#endif

enum io_priority_class {
	IO_PRIORITY_NONE,
	IO_PRIORITY_REALTIME,
	IO_PRIORITY_BEST_EFFORT,
	IO_PRIORITY_IDLE
};

struct priority_setting {
	int cpu;
	int io_class;
	int io_data;
};

struct priority_options {
	struct priority_setting local;
	struct priority_setting remote;
};

static struct priority_options priorities = {
	{ PRIORITY_UNSET, PRIORITY_UNSET, 0 },
	{ PRIORITY_UNSET, PRIORITY_UNSET, 0 }
};

static void clear_setting(struct priority_setting *setting)
{
	setting->cpu = PRIORITY_UNSET;
	setting->io_class = PRIORITY_UNSET;
	setting->io_data = 0;
}

void niceness_reset(void)
{
	clear_setting(&priorities.local);
	clear_setting(&priorities.remote);
}

void niceness_use_remote(void)
{
	priorities.local = priorities.remote;
	clear_setting(&priorities.remote);
}

static void default_setting(struct priority_setting *setting)
{
	setting->cpu = DEFAULT_NICE;
	setting->io_class = IO_PRIORITY_IDLE;
	setting->io_data = 0;
}

static uchar ascii_lower(uchar ch)
{
	return ch >= 'A' && ch <= 'Z' ? ch + ('a' - 'A') : ch;
}

static int token_equal(const char *str, size_t len, const char *word)
{
	size_t i;

	if (strlen(word) != len)
		return 0;
	for (i = 0; i < len; i++) {
		if (ascii_lower((uchar)str[i]) != ascii_lower((uchar)word[i]))
			return 0;
	}
	return 1;
}

static int parse_io_priority(const char *str, size_t len, int *class_p, int *data_p)
{
	if (token_equal(str, len, "none"))
		*class_p = IO_PRIORITY_NONE;
	else if (token_equal(str, len, "idle"))
		*class_p = IO_PRIORITY_IDLE;
	else if (len == 4 && str[2] == '_' && str[3] >= '0' && str[3] <= '7'
	      && (token_equal(str, 2, "rt") || token_equal(str, 2, "be"))) {
		*class_p = ascii_lower((uchar)str[0]) == 'r'
			? IO_PRIORITY_REALTIME : IO_PRIORITY_BEST_EFFORT;
		*data_p = str[3] - '0';
		return 1;
	} else
		return 0;

	*data_p = 0;
	return 1;
}

static int parse_priority(const char *str, const char *end, struct priority_setting *setting)
{
	char *num_end;
	long value;

	clear_setting(setting);
	if (parse_io_priority(str, end - str, &setting->io_class, &setting->io_data))
		return 1;
	if (!isdigit((uchar)*str)
	 && !((*str == '+' || *str == '-') && str + 1 < end
	   && isdigit((uchar)str[1])))
		return 0;

	errno = 0;
	value = strtol(str, &num_end, 10);
	if (errno == ERANGE || num_end == str || num_end > end || value < -20 || value > 19)
		return 0;
	setting->cpu = (int)value;
	if (num_end == end)
		return 1;
	if (*num_end++ != '/' || num_end == end)
		return 0;
	return parse_io_priority(num_end, end - num_end,
		&setting->io_class, &setting->io_data);
}

static int parse_target(const char *str, size_t len, int *local_p, int *remote_p)
{
	if (token_equal(str, len, "local")) {
		*local_p = 1;
		*remote_p = 0;
	} else if (token_equal(str, len, "remote")) {
		*local_p = 0;
		*remote_p = 1;
	} else if (token_equal(str, len, "all"))
		*local_p = *remote_p = 1;
	else
		return 0;
	return 1;
}

static void merge_setting(struct priority_setting *dest, const struct priority_setting *src)
{
	if (src->cpu != PRIORITY_UNSET)
		dest->cpu = src->cpu;
	if (src->io_class != PRIORITY_UNSET) {
		dest->io_class = src->io_class;
		dest->io_data = src->io_data;
	}
}

int niceness_parse(const char *arg)
{
	struct priority_options parsed = priorities;
	const char *str = arg;

	if (!str || !*str)
		return 0;

	while (*str) {
		struct priority_setting setting;
		const char *end = strchr(str, ',');
		const char *colon;
		int local = 1, remote = 1;

		if (!end)
			end = str + strlen(str);
		if (end == str)
			return 0;

		colon = memchr(str, ':', end - str);
		if (colon) {
			if (!parse_target(str, colon - str, &local, &remote)
			 || !parse_priority(colon + 1, end, &setting))
				return 0;
		} else if (parse_target(str, end - str, &local, &remote))
			default_setting(&setting);
		else if (!parse_priority(str, end, &setting))
			return 0;

		if (local)
			merge_setting(&parsed.local, &setting);
		if (remote)
			merge_setting(&parsed.remote, &setting);
		if (*end) {
			str = end + 1;
			if (!*str)
				return 0;
		} else
			str = end;
	}

	priorities = parsed;
	return 1;
}

static const char *io_priority_name(int class, int data, char *buf, size_t len)
{
	switch (class) {
	case IO_PRIORITY_NONE:
		return "none";
	case IO_PRIORITY_REALTIME:
		snprintf(buf, len, "rt_%d", data);
		return buf;
	case IO_PRIORITY_BEST_EFFORT:
		snprintf(buf, len, "be_%d", data);
		return buf;
	case IO_PRIORITY_IDLE:
		return "idle";
	}
	return "unknown";
}

char *niceness_remote_option(void)
{
	struct priority_setting *setting = &priorities.remote;
	char *arg;
	char io_buf[5];
	const char *io_name;
	int ret;

	if (setting->cpu == PRIORITY_UNSET && setting->io_class == PRIORITY_UNSET)
		return NULL;
	if (setting->io_class != PRIORITY_UNSET)
		io_name = io_priority_name(setting->io_class, setting->io_data,
			io_buf, sizeof io_buf);
	else
		io_name = NULL;
	if (setting->cpu != PRIORITY_UNSET && setting->io_class != PRIORITY_UNSET)
		ret = asprintf(&arg, "--nice=local:%d/%s", setting->cpu, io_name);
	else if (setting->cpu != PRIORITY_UNSET)
		ret = asprintf(&arg, "--nice=local:%d", setting->cpu);
	else
		ret = asprintf(&arg, "--nice=local:%s", io_name);
	if (ret < 0)
		out_of_memory("niceness_remote_option");
	return arg;
}

#ifdef SUPPORT_IO_PRIORITY
static int effective_io_priority(int class, int data, int cpu, int *rank_p, int *data_p)
{
	if (class == IOPRIO_CLASS_NONE) {
		class = IOPRIO_CLASS_BE;
		data = (cpu + 20) / 5;
		if (data > 7)
			data = 7;
	}

	switch (class) {
	case IOPRIO_CLASS_RT:
		*rank_p = 0;
		break;
	case IOPRIO_CLASS_BE:
		*rank_p = 1;
		break;
	case IOPRIO_CLASS_IDLE:
		*rank_p = 2;
		data = 0;
		break;
	default:
		return 0;
	}
	*data_p = data;
	return 1;
}

static int requested_io_priority(const struct priority_setting *setting, int cpu,
		int *rank_p, int *data_p)
{
	int class;

	switch (setting->io_class) {
	case IO_PRIORITY_NONE:
		class = IOPRIO_CLASS_NONE;
		break;
	case IO_PRIORITY_REALTIME:
		class = IOPRIO_CLASS_RT;
		break;
	case IO_PRIORITY_BEST_EFFORT:
		class = IOPRIO_CLASS_BE;
		break;
	case IO_PRIORITY_IDLE:
		class = IOPRIO_CLASS_IDLE;
		break;
	default:
		return 0;
	}
	return effective_io_priority(class, setting->io_data, cpu, rank_p, data_p);
}
#endif

int niceness_check_daemon_request(char *err_buf, size_t err_len)
{
	struct priority_setting *setting = &priorities.local;

	if (setting->cpu == PRIORITY_UNSET && setting->io_class == PRIORITY_UNSET)
		return 1;

#if defined SUPPORT_NICE_PRIORITY || defined SUPPORT_IO_PRIORITY
	int current_cpu;

	errno = 0;
	current_cpu = getpriority(PRIO_PROCESS, 0);
	if (errno) {
		snprintf(err_buf, err_len, "cannot inspect daemon process priority: %s\n",
			strerror(errno));
		return 0;
	}
#endif

#ifdef SUPPORT_NICE_PRIORITY
	if (setting->cpu != PRIORITY_UNSET && setting->cpu < current_cpu) {
		snprintf(err_buf, err_len, "--nice cannot raise daemon CPU priority\n");
		return 0;
	}
#endif

#ifdef SUPPORT_IO_PRIORITY
	if (setting->io_class != PRIORITY_UNSET) {
		int raw = syscall(SYS_ioprio_get, IOPRIO_WHO_PROCESS, 0);
		int requested_cpu = current_cpu;
		int current_rank, current_data, requested_rank, requested_data;

		if (raw < 0) {
			snprintf(err_buf, err_len, "cannot inspect daemon I/O priority: %s\n",
				strerror(errno));
			return 0;
		}
#ifdef SUPPORT_NICE_PRIORITY
		if (setting->cpu != PRIORITY_UNSET)
			requested_cpu = setting->cpu;
#endif
		/* IOPRIO_CLASS_NONE derives its level from CPU nice. */
		if (!effective_io_priority(IOPRIO_PRIO_CLASS(raw), RSYNC_IOPRIO_LEVEL(raw),
			current_cpu, &current_rank, &current_data)
		 || !requested_io_priority(setting, requested_cpu,
			&requested_rank, &requested_data)) {
			snprintf(err_buf, err_len, "unsupported daemon I/O priority class\n");
			return 0;
		}
		if (requested_rank < current_rank
		 || (requested_rank == current_rank && requested_data < current_data)) {
			snprintf(err_buf, err_len, "--nice cannot raise daemon I/O priority\n");
			return 0;
		}
	}
#endif

#if !defined SUPPORT_NICE_PRIORITY && !defined SUPPORT_IO_PRIORITY
	(void)err_buf;
	(void)err_len;
#endif

	return 1;
}

static const char *process_role(void)
{
	return am_daemon ? "daemon" : am_server ? "server" : "client";
}

static int apply_cpu_priority(int priority)
{
#ifdef SUPPORT_NICE_PRIORITY
	if (setpriority(PRIO_PROCESS, 0, priority) < 0) {
		rsyserr(FWARNING, errno, "failed to set %s CPU priority to %d",
			process_role(), priority);
		return 0;
	}
	if (DEBUG_GTE(CMD, 1))
		rprintf(FINFO, "set %s CPU priority to %d\n", process_role(), priority);
	return 1;
#else
	rprintf(FWARNING,
		"cannot set %s CPU priority to %d: "
		"operation is not supported on this system\n",
		process_role(), priority);
	return 0;
#endif
}

static void apply_io_priority(int class, int data)
{
	char name_buf[5];
	const char *name = io_priority_name(class, data, name_buf, sizeof name_buf);

#ifdef SUPPORT_IO_PRIORITY
	int kernel_class;

	switch (class) {
	case IO_PRIORITY_NONE:
		kernel_class = IOPRIO_CLASS_NONE;
		break;
	case IO_PRIORITY_REALTIME:
		kernel_class = IOPRIO_CLASS_RT;
		break;
	case IO_PRIORITY_BEST_EFFORT:
		kernel_class = IOPRIO_CLASS_BE;
		break;
	case IO_PRIORITY_IDLE:
		kernel_class = IOPRIO_CLASS_IDLE;
		break;
	default:
		return;
	}
	if (syscall(SYS_ioprio_set, IOPRIO_WHO_PROCESS, 0,
		IOPRIO_PRIO_VALUE(kernel_class, data)) < 0)
		rsyserr(FWARNING, errno, "failed to set %s I/O priority to %s",
			process_role(), name);
	else if (DEBUG_GTE(CMD, 1))
		rprintf(FINFO, "set %s I/O priority to %s\n", process_role(), name);
#else
	rprintf(FWARNING,
		"cannot set %s I/O priority to %s: "
		"operation is not supported on this system\n",
		process_role(), name);
#endif
}

void niceness_apply(void)
{
	struct priority_setting setting = priorities.local;
	int cpu_applied = 1;

	clear_setting(&priorities.local);
	if (setting.cpu != PRIORITY_UNSET)
		cpu_applied = apply_cpu_priority(setting.cpu);
#ifndef SUPPORT_NICE_PRIORITY
	(void)cpu_applied;
#endif
	if (setting.io_class != PRIORITY_UNSET) {
#ifdef SUPPORT_NICE_PRIORITY
		if (am_daemon && setting.cpu != PRIORITY_UNSET
		 && setting.io_class == IO_PRIORITY_NONE && !cpu_applied) {
			rprintf(FWARNING,
				"not resetting daemon I/O priority because the CPU priority change failed\n");
			return;
		}
#endif
		apply_io_priority(setting.io_class, setting.io_data);
	}
}
