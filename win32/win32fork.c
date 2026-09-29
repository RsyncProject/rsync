/*
 * fork()-like semantics for the generator/receiver split, using a thread.
 *
 * rsync's receiving side forks into a generator and a receiver that run
 * concurrently over a shared socket and a private pipe (do_recv() in main.c).
 * Windows has no fork(), so the receiver runs as a thread instead.
 *
 * A thread is not a process: it shares every global.  The state that the two
 * halves modify independently is therefore tagged RSYNC_TLS (see rsync.h) so
 * that it lives in the module's static thread-local block.  A fresh thread
 * would normally get the *initial* values of that block; fork gives the child
 * the parent's *current* values.  We reproduce that by copying the parent's
 * TLS block into the new thread before running any rsync code.
 *
 * Pointers inside that block still refer to shared heap, which fork would
 * have duplicated, so the buffers that hold in-flight protocol data get an
 * explicit deep copy in io_fork_child_fixup().
 *
 * Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
 * Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
 */

#include "rsync.h"
#include "win32/win32undef.h"

extern ULONG _tls_index;
extern IMAGE_TLS_DIRECTORY _tls_used;

static void *tls_block(void)
{
#ifdef _WIN64
	void **tls_array = (void **)__readgsqword(0x58);
#else
	void **tls_array = (void **)__readfsdword(0x2C);
#endif
	return tls_array[_tls_index];
}

static size_t tls_block_size(void)
{
	return (size_t)(_tls_used.EndAddressOfRawData - _tls_used.StartAddressOfRawData)
	     + _tls_used.SizeOfZeroFill;
}

struct fork_thread_ctx {
	void  (*fn)(void *);
	void   *arg;
	void   *parent_tls;
	size_t  tls_size;
	HANDLE  started;
};

static unsigned __stdcall fork_thread_trampoline(void *p)
{
	struct fork_thread_ctx *ctx = (struct fork_thread_ctx *)p;
	void (*fn)(void *) = ctx->fn;
	void *arg = ctx->arg;

	memcpy(tls_block(), ctx->parent_tls, ctx->tls_size);

	io_fork_child_fixup();

	SetEvent(ctx->started);

	fn(arg);
	return 0;
}

pid_t win32_fork_thread(void (*fn)(void *), void *arg)
{

	struct fork_thread_ctx ctx;
	uintptr_t th;
	unsigned tid = 0;

	ctx.fn = fn;
	ctx.arg = arg;
	ctx.tls_size = tls_block_size();
	ctx.parent_tls = malloc(ctx.tls_size);
	if (!ctx.parent_tls) {
		errno = ENOMEM;
		return -1;
	}
	memcpy(ctx.parent_tls, tls_block(), ctx.tls_size);

	ctx.started = CreateEvent(NULL, TRUE, FALSE, NULL);
	if (!ctx.started) {
		free(ctx.parent_tls);
		errno = EAGAIN;
		return -1;
	}

	th = _beginthreadex(NULL, 0, fork_thread_trampoline, &ctx, 0, &tid);
	if (!th) {
		CloseHandle(ctx.started);
		free(ctx.parent_tls);
		errno = EAGAIN;
		return -1;
	}

	WaitForSingleObject(ctx.started, INFINITE);
	CloseHandle(ctx.started);
	free(ctx.parent_tls);

	win32_remember_thread_child((pid_t)tid, (HANDLE)th);
	return (pid_t)tid;
}
