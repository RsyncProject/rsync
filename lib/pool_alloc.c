#include "rsync.h"

#define POOL_DEF_EXTENT	(32 * 1024)

struct alloc_pool
{
	size_t			size;		/* extent size		*/
	size_t			quantum;	/* allocation quantum	*/
	struct pool_extent	*live;		/* current extent for
						 * allocations		*/
	struct pool_extent	*free;		/* unfreed extent list	*/
	void			(*bomb)();	/* function to call if
						 * malloc fails		*/
	int			flags;

	/* statistical data */
	unsigned long		e_created;	/* extents created	*/
	unsigned long		e_freed;	/* extents detroyed	*/
	int64			n_allocated;	/* calls to alloc	*/
	int64			n_freed;	/* calls to free	*/
	int64			b_allocated;	/* cum. bytes allocated	*/
	int64			b_freed;	/* cum. bytes freed	*/
};

struct pool_extent
{
	void			*start;		/* starting address	*/
	size_t			free;		/* free bytecount	*/
	size_t			bound;		/* bytes bound by padding,
						 * overhead and freed	*/
	struct pool_extent	*next;
};

struct align_test {
	void *foo;
	int64 bar;
};

#define MINALIGN	offsetof(struct align_test, bar)

/* Temporarily cast a void* var into a char* var when adding an offset (to
 * keep some compilers from complaining about the pointer arithmetic). */
#define PTR_ADD(b,o)	( (void*) ((char*)(b) + (o)) )

alloc_pool_t
pool_create(size_t size, size_t quantum, void (*bomb)(const char *), int flags)
{
	struct alloc_pool	*pool;

	if (!(pool = new(struct alloc_pool)))
		return pool;
	memset(pool, 0, sizeof (struct alloc_pool));

	pool->size = size	/* round extent size to min alignment reqs */
	    ? (size + MINALIGN - 1) & ~(MINALIGN - 1)
	    : POOL_DEF_EXTENT;
	if (pool->flags & POOL_INTERN) {
		pool->size -= sizeof (struct pool_extent);
		flags |= POOL_APPEND;
	}
	pool->quantum = quantum ? quantum : MINALIGN;
	pool->bomb = bomb;
	pool->flags = flags;

	return pool;
}

void
pool_destroy(alloc_pool_t p)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	struct pool_extent	*cur, *next;

	if (!pool)
		return;

	if (pool->live) {
		cur = pool->live;
		free(cur->start);
		if (!(pool->flags & POOL_APPEND))
			free(cur);
	}
	for (cur = pool->free; cur; cur = next) {
		next = cur->next;
		free(cur->start);
		if (!(pool->flags & POOL_APPEND))
			free(cur);
	}
	free(pool);
}

void *
pool_alloc(alloc_pool_t p, size_t len, const char *bomb)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	if (!pool)
		return NULL;

	if (!len)
		len = pool->quantum;
	else if (pool->quantum > 1 && len % pool->quantum)
		len += pool->quantum - len % pool->quantum;

	if (len > pool->size)
		goto bomb;

	if (!pool->live || len > pool->live->free) {
		void	*start;
		size_t	free;
		size_t	bound;
		size_t	skew;
		size_t	asize;
		struct pool_extent *ext;

		if (pool->live) {
			pool->live->next = pool->free;
			pool->free = pool->live;
		}

		free = pool->size;
		bound = 0;

		asize = pool->size;
		if (pool->flags & POOL_APPEND)
			asize += sizeof (struct pool_extent);

		if (!(start = new_array(char, asize)))
			goto bomb;

		if (pool->flags & POOL_CLEAR)
			memset(start, 0, free);

		if (pool->flags & POOL_APPEND)
			ext = PTR_ADD(start, free);
		else if (!(ext = new(struct pool_extent)))
			goto bomb;
		if (pool->flags & POOL_QALIGN && pool->quantum > 1
		    && (skew = (size_t)PTR_ADD(start, free) % pool->quantum)) {
			bound  += skew;
			free -= skew;
		}
		ext->start = start;
		ext->free = free;
		ext->bound = bound;
		ext->next = NULL;
		pool->live = ext;

		pool->e_created++;
	}

	pool->n_allocated++;
	pool->b_allocated += len;

	pool->live->free -= len;

	return PTR_ADD(pool->live->start, pool->live->free);

bomb:
	if (pool->bomb)
		(*pool->bomb)(bomb);
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

	if (!len)
		len = pool->quantum;
	else if (pool->quantum > 1 && len % pool->quantum)
		len += pool->quantum - len % pool->quantum;

	if (!addr && pool->live) {
		pool->live->next = pool->free;
		pool->free = pool->live;
		pool->live = NULL;
		return;
	}
	pool->n_freed++;
	pool->b_freed += len;

	cur = pool->live;
	if (cur && addr >= cur->start
	    && addr < PTR_ADD(cur->start, pool->size)) {
		if (addr == PTR_ADD(cur->start, cur->free)) {
			if (pool->flags & POOL_CLEAR)
				memset(addr, 0, len);
			cur->free += len;
		} else
			cur->bound += len;
		if (cur->free + cur->bound >= pool->size) {
			size_t skew;

			if (pool->flags & POOL_CLEAR) {
				memset(PTR_ADD(cur->start, cur->free), 0,
				       pool->size - cur->free);
			}
			cur->free = pool->size;
			cur->bound = 0;
			if (pool->flags & POOL_QALIGN && pool->quantum > 1
			    && (skew = (size_t)PTR_ADD(cur->start, cur->free) % pool->quantum)) {
				cur->bound += skew;
				cur->free -= skew;
			}
		}
		return;
	}
	for (prev = NULL, cur = pool->free; cur; prev = cur, cur = cur->next) {
		if (addr >= cur->start
		    && addr < PTR_ADD(cur->start, pool->size))
			break;
	}
	if (!cur)
		return;

	if (prev) {
		prev->next = cur->next;
		cur->next = pool->free;
		pool->free = cur;
	}
	cur->bound += len;

	if (cur->free + cur->bound >= pool->size) {
		pool->free = cur->next;

		free(cur->start);
		if (!(pool->flags & POOL_APPEND))
			free(cur);
		pool->e_freed++;
	}
}

#define FDPRINT(label, value) \
	snprintf(buf, sizeof buf, label, value), \
	write(fd, buf, strlen(buf))

#define FDEXTSTAT(ext) \
	snprintf(buf, sizeof buf, "  %12ld  %5ld\n", \
		(long) ext->free, \
		(long) ext->bound), \
	write(fd, buf, strlen(buf))

void
pool_stats(alloc_pool_t p, int fd, int summarize)
{
	struct alloc_pool *pool = (struct alloc_pool *) p;
	struct pool_extent	*cur;
	char buf[BUFSIZ];

	if (!pool)
		return;

	FDPRINT("  Extent size:       %12ld\n",	(long)	pool->size);
	FDPRINT("  Alloc quantum:     %12ld\n",	(long)	pool->quantum);
	FDPRINT("  Extents created:   %12ld\n",		pool->e_created);
	FDPRINT("  Extents freed:     %12ld\n",		pool->e_freed);
	FDPRINT("  Alloc count:       %12.0f\n", (double) pool->n_allocated);
	FDPRINT("  Free Count:        %12.0f\n", (double) pool->n_freed);
	FDPRINT("  Bytes allocated:   %12.0f\n", (double) pool->b_allocated);
	FDPRINT("  Bytes freed:       %12.0f\n", (double) pool->b_freed);

	if (summarize)
		return;

	if (!pool->live && !pool->free)
		return;

	write(fd, "\n", 1);

	if (pool->live)
		FDEXTSTAT(pool->live);
	strlcpy(buf, "   FREE    BOUND\n", sizeof buf);
	write(fd, buf, strlen(buf));

	for (cur = pool->free; cur; cur = cur->next)
		FDEXTSTAT(cur);
}
