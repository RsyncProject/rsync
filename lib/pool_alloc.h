#include <stddef.h>

#define POOL_CLEAR	(1<<0)		/* zero fill allocations	*/
#define POOL_QALIGN	(1<<1)		/* align data to quanta		*/
#define POOL_INTERN	(1<<2)		/* Allocate extent structures	*/
#define POOL_APPEND	(1<<3)		/*   or appended to extent data	*/

typedef void *alloc_pool_t;

alloc_pool_t pool_create(size_t size, size_t quantum, void (*bomb)(const char *), int flags);
void pool_destroy(alloc_pool_t pool);
void *pool_alloc(alloc_pool_t pool, size_t size, const char *bomb_msg);
void pool_free(alloc_pool_t pool, size_t size, void *addr);
void pool_free_old(alloc_pool_t pool, void *addr);
void *pool_boundary(alloc_pool_t pool, size_t size);

#define pool_talloc(pool, type, count, bomb_msg) \
	((type *)pool_alloc(pool, sizeof(type) * count, bomb_msg))

#define pool_tfree(pool, type, count, addr) \
	(pool_free(pool, sizeof(type) * count, addr))
