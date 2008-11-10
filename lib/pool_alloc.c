#include "rsync.h"

#define POOL_DEF_EXTENT	(32 * 1024)

#define POOL_QALIGN_P2		(1<<16)		/* power-of-2 qalign	*/

struct alloc_pool
{
	size_t			size;		/* extent size		*/
	size_t			quantum;	/* allocation quantum	*/
	struct pool_extent	*extents;	/* top extent is "live" */
	void			(*bomb)();	/* function to call if
						 * malloc fails		*/
	int			flags;

	/* statistical data */
	unsigned long		e_created;	/* extents created	*/
	unsigned long		e_freed;	/* extents destroyed	*/
	int64			n_allocated;	/* calls to alloc	*/
	int64			n_freed;	/* calls to free	*/
	int64			b_allocated;	/* cum. bytes allocated	*/
	int64			b_freed;	/* cum. bytes freed	*/
};

struct pool_extent
{
	struct pool_extent	*next;
	void			*start;		/* starting address	*/
	size_t			free;		/* free bytecount	*/
	size_t			bound;		/* trapped free bytes	*/
};

struct align_test {
	uchar foo;
	union {
	    int64 i;
	    void *p;
	} bar;
};

#define MINALIGN	offsetof(struct align_test, bar)

/* Temporarily cast a void* var into a char* var when adding an offset (to
 * keep some compilers from complaining about the pointer arithmetic). */
#define PTR_ADD(b,o)	( (void*) ((char*)(b) + (o)) )

alloc_pool_t
pool_create(size_t size, size_t quantum, void (*bomb)(const char *), int flags)
{
	struct alloc_pool *pool;

	if (!(pool = new0(struct alloc_pool)))
		return NULL;

	if ((MINALIGN & (MINALIGN - 1)) != 0) {
		if (bomb)
			(*bomb)("Compiler error: MINALIGN is not a power of 2\n");
		return NULL;
	}

	if (!size)
		size = POOL_DEF_EXTENT;
	if (!quantum)
		quantum = MINALIGN;

	if (flags & POOL_INTERN) {
		if (size <= sizeof (struct pool_extent))
			size = quantum;
		else
			size -= sizeof (struct pool_extent);
		flags |= POOL_PREPEND;
	}

	if (quantum <= 1)
		flags = (flags | POOL_NO_QALIGN) & ~POOL_QALIGN_P2;
	else if (!(flags & POOL_NO_QALIGN)) {
		if (size % quantum)
			size += quantum - size % quantum;
		/* If quantum is a power of 2, we'll avoid using modulus. */
		if (!(quantum & (quantum - 1)))
			flags |= POOL_QALIGN_P2;
	}

	pool->size = size;
	pool->quantum = quantum;
	pool->bomb = bomb;
	pool->flags = flags;

	return pool;
}

void
pool_destroy(alloc_pool_t p)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	struct pool_extent *cur, *next;

	if (!pool)
		return;

	for (cur = pool->extents; cur; cur = next) {
		next = cur->next;
		if (pool->flags & POOL_PREPEND)
			free(PTR_ADD(cur->start, -sizeof (struct pool_extent)));
		else {
			free(cur->start);
			free(cur);
		}
	}

	free(pool);
}

void *
pool_alloc(alloc_pool_t p, size_t len, const char *bomb_msg)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	if (!pool)
		return NULL;

	if (!len)
		len = pool->quantum;
	else if (pool->flags & POOL_QALIGN_P2) {
		if (len & (pool->quantum - 1))
			len += pool->quantum - (len & (pool->quantum - 1));
	} else if (!(pool->flags & POOL_NO_QALIGN)) {
		if (len % pool->quantum)
			len += pool->quantum - len % pool->quantum;
	}

	if (len > pool->size)
		goto bomb_out;

	if (!pool->extents || len > pool->extents->free) {
		void *start;
		size_t asize;
		struct pool_extent *ext;

		asize = pool->size;
		if (pool->flags & POOL_PREPEND)
			asize += sizeof (struct pool_extent);

		if (!(start = new_array(char, asize)))
			goto bomb_out;

		if (pool->flags & POOL_CLEAR)
			memset(start, 0, asize);

		if (pool->flags & POOL_PREPEND) {
			ext = start;
			start = PTR_ADD(start, sizeof (struct pool_extent));
		} else if (!(ext = new(struct pool_extent)))
			goto bomb_out;
		ext->start = start;
		ext->free = pool->size;
		ext->bound = 0;
		ext->next = pool->extents;
		pool->extents = ext;

		pool->e_created++;
	}

	pool->n_allocated++;
	pool->b_allocated += len;

	pool->extents->free -= len;

	return PTR_ADD(pool->extents->start, pool->extents->free);

  bomb_out:
	if (pool->bomb)
		(*pool->bomb)(bomb_msg);
	return NULL;
}

/* This function allows you to declare memory in the pool that you are done
 * using.  If you free all the memory in a pool's extent, that extent will
 * be freed. */
void
pool_free(alloc_pool_t p, size_t len, void *addr)
{
	struct alloc_pool *pool = (struct alloc_pool *)p;
	struct pool_extent *cur, *prev;

	if (!pool)
		return;

	if (!addr) {
		/* A NULL addr starts a fresh extent for new allocations. */
		if ((cur = pool->extents) != NULL && cur->free != pool->size) {
			cur->bound += cur->free;
			cur->free = 0;
		}
		return;
	}

	if (!len)
		len = pool->quantum;
	else if (pool->flags & POOL_QALIGN_P2) {
		if (len & (pool->quantum - 1))
			len += pool->quantum - (len & (pool->quantum - 1));
	} else if (!(pool->flags & POOL_NO_QALIGN)) {
		if (len % pool->quantum)
			len += pool->quantum - len % pool->quantum;
	}

	pool->n_freed++;
	pool->b_freed += len;

	for (prev = NULL, cur = pool->extents; cur; prev = cur, cur = cur->next) {
		if (addr >= cur->start
		    && addr < PTR_ADD(cur->start, pool->size))
			break;
	}
	if (!cur)
		return;

	if (!prev) {
		/* The "live" extent is kept ready for more allocations. */
		if (cur->free + cur->bound + len >= pool->size) {
			if (pool->flags & POOL_CLEAR) {
				memset(PTR_ADD(cur->start, cur->free), 0,
				       pool->size - cur->free);
			}
			cur->free = pool->size;
			cur->bound = 0;
		} else if (addr == PTR_ADD(cur->start, cur->free)) {
			if (pool->flags & POOL_CLEAR)
				memset(addr, 0, len);
			cur->free += len;
		} else
			cur->bound += len;
	} else {
		cur->bound += len;

		if (cur->free + cur->bound >= pool->size) {
			prev->next = cur->next;
			if (pool->flags & POOL_PREPEND)
				free(PTR_ADD(cur->start, -sizeof (struct pool_extent)));
			else {
				free(cur->start);
				free(cur);
			}
			pool->e_freed++;
		} else if (prev != pool->extents) {
			/* Move the extent to be the first non-live extent. */
			prev->next = cur->next;
			cur->next = pool->extents->next;
			pool->extents->next = cur;
		}
	}
}

/* This allows you to declare that the given address marks the edge of some
 * pool memory that is no longer needed.  Any extents that hold only data
 * older than the boundary address are freed.  NOTE: You MUST NOT USE BOTH
 * pool_free() and pool_free_old() on the same pool!! */
void
pool_free_old(alloc_pool_t p, void *addr)
{
	struct alloc_pool *pool = (struct alloc_pool *)p;
	struct pool_extent *cur, *prev, *next;

	if (!pool || !addr)
		return;

	for (prev = NULL, cur = pool->extents; cur; prev = cur, cur = cur->next) {
		if (addr >= cur->start
		    && addr < PTR_ADD(cur->start, pool->size))
			break;
	}
	if (!cur)
		return;

	if (addr == PTR_ADD(cur->start, cur->free)) {
		if (prev) {
			prev->next = NULL;
			next = cur;
		} else {
			/* The most recent live extent can just be reset. */
			if (pool->flags & POOL_CLEAR)
				memset(addr, 0, pool->size - cur->free);
			cur->free = pool->size;
			cur->bound = 0;
			next = cur->next;
			cur->next = NULL;
		}
	} else {
		next = cur->next;
		cur->next = NULL;
	}

	while ((cur = next) != NULL) {
		next = cur->next;
		if (pool->flags & POOL_PREPEND)
			free(PTR_ADD(cur->start, -sizeof (struct pool_extent)));
		else {
			free(cur->start);
			free(cur);
		}
		pool->e_freed++;
	}
}

/* If the current extent doesn't have "len" free space in it, mark it as full
 * so that the next alloc will start a new extent.  If len is (size_t)-1, this
 * bump will always occur.  The function returns a boundary address that can
 * be used with pool_free_old(), or a NULL if no memory is allocated. */
void *
pool_boundary(alloc_pool_t p, size_t len)
{
	struct alloc_pool *pool = (struct alloc_pool *)p;
	struct pool_extent *cur;

	if (!pool || !pool->extents)
		return NULL;

	cur = pool->extents;

	if (cur->free < len) {
		cur->bound += cur->free;
		cur->free = 0;
	}

	return PTR_ADD(cur->start, cur->free);
}

#define FDPRINT(label, value) \
	do { \
		int len = snprintf(buf, sizeof buf, label, value); \
		if (write(fd, buf, len) != len) \
			ret = -1; \
	} while (0)

#define FDEXTSTAT(ext) \
	do { \
		int len = snprintf(buf, sizeof buf, "  %12ld  %5ld\n", \
				   (long)ext->free, (long)ext->bound); \
		if (write(fd, buf, len) != len) \
			ret = -1; \
	} while (0)

int
pool_stats(alloc_pool_t p, int fd, int summarize)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	struct pool_extent *cur;
	char buf[BUFSIZ];
	int ret = 0;

	if (!pool)
		return ret;

	FDPRINT("  Extent size:       %12ld\n",	(long)	pool->size);
	FDPRINT("  Alloc quantum:     %12ld\n",	(long)	pool->quantum);
	FDPRINT("  Extents created:   %12ld\n",		pool->e_created);
	FDPRINT("  Extents freed:     %12ld\n",		pool->e_freed);
	FDPRINT("  Alloc count:       %12.0f\n", (double) pool->n_allocated);
	FDPRINT("  Free Count:        %12.0f\n", (double) pool->n_freed);
	FDPRINT("  Bytes allocated:   %12.0f\n", (double) pool->b_allocated);
	FDPRINT("  Bytes freed:       %12.0f\n", (double) pool->b_freed);

	if (summarize)
		return ret;

	if (!pool->extents)
		return ret;

	if (write(fd, "\n", 1) != 1)
		ret = -1;

	for (cur = pool->extents; cur; cur = cur->next)
		FDEXTSTAT(cur);

	return ret;
}
