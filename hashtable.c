/*
 * Routines to provide a memory-efficient hashtable.
 *
 * Copyright (C) 2007-2022 Wayne Davison
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, visit the http://fsf.org website.
 */

#include "rsync.h"

#define HASH_LOAD_LIMIT(size) ((size)*3/4)

struct hashtable *hashtable_create(int size, int key64)
{
	int req = size;
	struct hashtable *tbl;
	int node_size = key64 ? sizeof (struct ht_int64_node)
			      : sizeof (struct ht_int32_node);

	/* Pick a power of 2 that can hold the requested size. */
	if (size & (size-1) || size < 16) {
		size = 16;
		while (size < req)
			size *= 2;
	}

	tbl = new(struct hashtable);
	tbl->nodes = new_array0(char, size * node_size);
	tbl->size = size;
	tbl->entries = 0;
	tbl->node_size = node_size;
	tbl->key64 = key64 ? 1 : 0;

	if (DEBUG_GTE(HASH, 1)) {
		char buf[32];
		if (req != size)
			snprintf(buf, sizeof buf, "req: %d, ", req);
		else
			*buf = '\0';
		rprintf(FINFO, "[%s] created hashtable %lx (%ssize: %d, keys: %d-bit)\n",
			who_am_i(), (long)tbl, buf, size, key64 ? 64 : 32);
	}

	return tbl;
}

void hashtable_destroy(struct hashtable *tbl)
{
	if (DEBUG_GTE(HASH, 1)) {
		rprintf(FINFO, "[%s] destroyed hashtable %lx (size: %d, keys: %d-bit)\n",
			who_am_i(), (long)tbl, tbl->size, tbl->key64 ? 64 : 32);
	}
	free(tbl->nodes);
	free(tbl);
}

/* Returns the node that holds the indicated key if it exists. When it does not
 * exist, it returns either NULL (when data_when_new is NULL), or it returns a
 * new node with its node->data set to the indicated value.
 *
 * If your code doesn't know the data value for a new node in advance (usually
 * because it doesn't know if a node is new or not) you should pass in a unique
 * (non-0) value that you can use to check if the returned node is new. You can
 * then overwrite the data with any value you want (even 0) since it only needs
 * to be different than whatever data_when_new value you use later on.
 *
 * This return is a void* just because it might be pointing at a ht_int32_node
 * or a ht_int64_node, and that makes the caller's assignment a little easier. */
void *hashtable_find(struct hashtable *tbl, int64 key, void *data_when_new)
{
	int key64 = tbl->key64;
	struct ht_int32_node *node;
	uint32 ndx;

	if (key64 ? key == 0 : (int32)key == 0) {
		rprintf(FERROR, "Internal hashtable error: illegal key supplied!\n");
		exit_cleanup(RERR_MESSAGEIO);
	}

	if (data_when_new && tbl->entries > HASH_LOAD_LIMIT(tbl->size)) {
		void *old_nodes = tbl->nodes;
		int size = tbl->size * 2;
		int i;

		tbl->nodes = new_array0(char, size * tbl->node_size);
		tbl->size = size;
		tbl->entries = 0;

		if (DEBUG_GTE(HASH, 1)) {
			rprintf(FINFO, "[%s] growing hashtable %lx (size: %d, keys: %d-bit)\n",
				who_am_i(), (long)tbl, size, key64 ? 64 : 32);
		}

		for (i = size / 2; i-- > 0; ) {
			struct ht_int32_node *move_node = HT_NODE(tbl, old_nodes, i);
			int64 move_key = HT_KEY(move_node, key64);
			if (move_key == 0)
				continue;
			if (move_node->data)
				hashtable_find(tbl, move_key, move_node->data);
			else {
				node = hashtable_find(tbl, move_key, "");
				node->data = 0;
			}
		}

		free(old_nodes);
	}

	if (!key64) {
		/* Based on Jenkins One-at-a-time hash. */
		uchar buf[4], *keyp = buf;
		int i;

		SIVALu(buf, 0, key);
		for (ndx = 0, i = 0; i < 4; i++) {
			ndx += keyp[i];
			ndx += (ndx << 10);
			ndx ^= (ndx >> 6);
		}
		ndx += (ndx << 3);
		ndx ^= (ndx >> 11);
		ndx += (ndx << 15);
	} else {
		/* Based on Jenkins hashword() from lookup3.c. */
		uint32 a, b, c;

		/* Set up the internal state */
		a = b = c = 0xdeadbeef + (8 << 2);

#define rot(x,k) (((x)<<(k)) ^ ((x)>>(32-(k))))
#if SIZEOF_INT64 >= 8
		b += (uint32)(key >> 32);
#endif
		a += (uint32)key;
		c ^= b; c -= rot(b, 14);
		a ^= c; a -= rot(c, 11);
		b ^= a; b -= rot(a, 25);
		c ^= b; c -= rot(b, 16);
		a ^= c; a -= rot(c, 4);
		b ^= a; b -= rot(a, 14);
		c ^= b; c -= rot(b, 24);
#undef rot
		ndx = c;
	}

	/* If it already exists, return the node.  If we're not
	 * allocating, return NULL if the key is not found. */
	while (1) {
		int64 nkey;

		ndx &= tbl->size - 1;
		node = HT_NODE(tbl, tbl->nodes, ndx);
		nkey = HT_KEY(node, key64);

		if (nkey == key)
			return node;
		if (nkey == 0) {
			if (!data_when_new)
				return NULL;
			break;
		}
		ndx++;
	}

	/* Take over this empty spot and then return the node. */
	if (key64)
		((struct ht_int64_node*)node)->key = key;
	else
		node->key = (int32)key;
	node->data = data_when_new;
	tbl->entries++;
	return node;
}

#ifndef WORDS_BIGENDIAN
# define HASH_LITTLE_ENDIAN 1
# define HASH_BIG_ENDIAN 0
#else
# define HASH_LITTLE_ENDIAN 0
# define HASH_BIG_ENDIAN 1
#endif

/*
 -------------------------------------------------------------------------------
 lookup3.c, by Bob Jenkins, May 2006, Public Domain.

 These are functions for producing 32-bit hashes for hash table lookup.
 hash_word(), hashlittle(), hashlittle2(), hashbig(), mix(), and final()
 are externally useful functions.  Routines to test the hash are included
 if SELF_TEST is defined.  You can use this free for any purpose.  It's in
 the public domain.  It has no warranty.

 You probably want to use hashlittle().  hashlittle() and hashbig()
 hash byte arrays.  hashlittle() is is faster than hashbig() on
 little-endian machines.  Intel and AMD are little-endian machines.
 On second thought, you probably want hashlittle2(), which is identical to
 hashlittle() except it returns two 32-bit hashes for the price of one.
 You could implement hashbig2() if you wanted but I haven't bothered here.

 If you want to find a hash of, say, exactly 7 integers, do
   a = i1;  b = i2;  c = i3;
   mix(a,b,c);
   a += i4; b += i5; c += i6;
   mix(a,b,c);
   a += i7;
   final(a,b,c);
 then use c as the hash value.  If you have a variable length array of
 4-byte integers to hash, use hash_word().  If you have a byte array (like
 a character string), use hashlittle().  If you have several byte arrays, or
 a mix of things, see the comments above hashlittle().

 Why is this so big?  I read 12 bytes at a time into 3 4-byte integers,
 then mix those integers.  This is fast (you can do a lot more thorough
 mixing with 12*3 instructions on 3 integers than you can with 3 instructions
 on 1 byte), but shoehorning those bytes into integers efficiently is messy.
*/

#define hashsize(n) ((uint32_t)1<<(n))
#define hashmask(n) (hashsize(n)-1)
#define rot(x,k) (((x)<<(k)) | ((x)>>(32-(k))))

/*
 -------------------------------------------------------------------------------
 mix -- mix 3 32-bit values reversibly.

 This is reversible, so any information in (a,b,c) before mix() is
 still in (a,b,c) after mix().

 If four pairs of (a,b,c) inputs are run through mix(), or through
 mix() in reverse, there are at least 32 bits of the output that
 are sometimes the same for one pair and different for another pair.
 This was tested for:
 * pairs that differed by one bit, by two bits, in any combination
   of top bits of (a,b,c), or in any combination of bottom bits of
   (a,b,c).
 * "differ" is defined as +, -, ^, or ~^.  For + and -, I transformed
   the output delta to a Gray code (a^(a>>1)) so a string of 1's (as
   is commonly produced by subtraction) look like a single 1-bit
   difference.
 * the base values were pseudorandom, all zero but one bit set, or
   all zero plus a counter that starts at zero.

 Some k values for my "a-=c; a^=rot(c,k); c+=b;" arrangement that
 satisfy this are
     4  6  8 16 19  4
     9 15  3 18 27 15
    14  9  3  7 17  3
 Well, "9 15 3 18 27 15" didn't quite get 32 bits diffing
 for "differ" defined as + with a one-bit base and a two-bit delta.  I
 used http://burtleburtle.net/bob/hash/avalanche.html to choose
 the operations, constants, and arrangements of the variables.

 This does not achieve avalanche.  There are input bits of (a,b,c)
 that fail to affect some output bits of (a,b,c), especially of a.  The
 most thoroughly mixed value is c, but it doesn't really even achieve
 avalanche in c.

 This allows some parallelism.  Read-after-writes are good at doubling
 the number of bits affected, so the goal of mixing pulls in the opposite
 direction as the goal of parallelism.  I did what I could.  Rotates
 seem to cost as much as shifts on every machine I could lay my hands
 on, and rotates are much kinder to the top and bottom bits, so I used
 rotates.
 -------------------------------------------------------------------------------
*/
#define mix(a,b,c) \
{ \
  a -= c;  a ^= rot(c, 4);  c += b; \
  b -= a;  b ^= rot(a, 6);  a += c; \
  c -= b;  c ^= rot(b, 8);  b += a; \
  a -= c;  a ^= rot(c,16);  c += b; \
  b -= a;  b ^= rot(a,19);  a += c; \
  c -= b;  c ^= rot(b, 4);  b += a; \
}

/*
 -------------------------------------------------------------------------------
 final -- final mixing of 3 32-bit values (a,b,c) into c

 Pairs of (a,b,c) values differing in only a few bits will usually
 produce values of c that look totally different.  This was tested for
 * pairs that differed by one bit, by two bits, in any combination
   of top bits of (a,b,c), or in any combination of bottom bits of
   (a,b,c).
 * "differ" is defined as +, -, ^, or ~^.  For + and -, I transformed
   the output delta to a Gray code (a^(a>>1)) so a string of 1's (as
   is commonly produced by subtraction) look like a single 1-bit
   difference.
 * the base values were pseudorandom, all zero but one bit set, or
   all zero plus a counter that starts at zero.

 These constants passed:
  14 11 25 16 4 14 24
  12 14 25 16 4 14 24
 and these came close:
   4  8 15 26 3 22 24
  10  8 15 26 3 22 24
  11  8 15 26 3 22 24
 -------------------------------------------------------------------------------
*/
#define final(a,b,c) \
{ \
  c ^= b; c -= rot(b,14); \
  a ^= c; a -= rot(c,11); \
  b ^= a; b -= rot(a,25); \
  c ^= b; c -= rot(b,16); \
  a ^= c; a -= rot(c,4);  \
  b ^= a; b -= rot(a,14); \
  c ^= b; c -= rot(b,24); \
}


/*
 -------------------------------------------------------------------------------
 hashlittle() -- hash a variable-length key into a 32-bit value
   k       : the key (the unaligned variable-length array of bytes)
   length  : the length of the key, counting by bytes
   val2    : IN: can be any 4-byte value OUT: second 32 bit hash.
 Returns a 32-bit value.  Every bit of the key affects every bit of
 the return value.  Two keys differing by one or two bits will have
 totally different hash values.  Note that the return value is better
 mixed than val2, so use that first.

 The best hash table sizes are powers of 2.  There is no need to do
 mod a prime (mod is sooo slow!).  If you need less than 32 bits,
 use a bitmask.  For example, if you need only 10 bits, do
   h = (h & hashmask(10));
 In which case, the hash table should have hashsize(10) elements.

 If you are hashing n strings (uint8_t **)k, do it like this:
   for (i=0, h=0; i<n; ++i) h = hashlittle( k[i], len[i], h);

 By Bob Jenkins, 2006.  bob_jenkins@burtleburtle.net.  You may use this
 code any way you wish, private, educational, or commercial.  It's free.

 Use for hash table lookup, or anything where one collision in 2^^32 is
 acceptable.  Do NOT use for cryptographic purposes.
 -------------------------------------------------------------------------------
*/

#define NON_ZERO_32(x) ((x) ? (x) : (uint32_t)1)
#define NON_ZERO_64(x, y) ((x) || (y) ? (y) | (int64)(x) << 32 | (y) : (int64)1)

uint32_t hashlittle(const void *key, size_t length)
{
  uint32_t a,b,c;                                          /* internal state */
  union { const void *ptr; size_t i; } u;     /* needed for Mac Powerbook G4 */

  /* Set up the internal state */
  a = b = c = 0xdeadbeef + ((uint32_t)length);

  u.ptr = key;
  if (HASH_LITTLE_ENDIAN && ((u.i & 0x3) == 0)) {
    const uint32_t *k = (const uint32_t *)key;         /* read 32-bit chunks */
    const uint8_t  *k8;

    /*------ all but last block: aligned reads and affect 32 bits of (a,b,c) */
    while (length > 12)
    {
      a += k[0];
      b += k[1];
      c += k[2];
      mix(a,b,c);
      length -= 12;
      k += 3;
    }

    /*----------------------------- handle the last (probably partial) block */
    k8 = (const uint8_t *)k;
    switch(length)
    {
    case 12: c+=k[2]; b+=k[1]; a+=k[0]; break;
    case 11: c+=((uint32_t)k8[10])<<16;  /* fall through */
    case 10: c+=((uint32_t)k8[9])<<8;    /* fall through */
    case 9 : c+=k8[8];                   /* fall through */
    case 8 : b+=k[1]; a+=k[0]; break;
    case 7 : b+=((uint32_t)k8[6])<<16;   /* fall through */
    case 6 : b+=((uint32_t)k8[5])<<8;    /* fall through */
    case 5 : b+=k8[4];                   /* fall through */
    case 4 : a+=k[0]; break;
    case 3 : a+=((uint32_t)k8[2])<<16;   /* fall through */
    case 2 : a+=((uint32_t)k8[1])<<8;    /* fall through */
    case 1 : a+=k8[0]; break;
    case 0 : return NON_ZERO_32(c);
    }
  } else if (HASH_LITTLE_ENDIAN && ((u.i & 0x1) == 0)) {
    const uint16_t *k = (const uint16_t *)key;         /* read 16-bit chunks */
    const uint8_t  *k8;

    /*--------------- all but last block: aligned reads and different mixing */
    while (length > 12)
    {
      a += k[0] + (((uint32_t)k[1])<<16);
      b += k[2] + (((uint32_t)k[3])<<16);
      c += k[4] + (((uint32_t)k[5])<<16);
      mix(a,b,c);
      length -= 12;
      k += 6;
    }

    /*----------------------------- handle the last (probably partial) block */
    k8 = (const uint8_t *)k;
    switch(length)
    {
    case 12: c+=k[4]+(((uint32_t)k[5])<<16);
             b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 11: c+=((uint32_t)k8[10])<<16;     /* fall through */
    case 10: c+=k[4];
             b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 9 : c+=k8[8];                      /* fall through */
    case 8 : b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 7 : b+=((uint32_t)k8[6])<<16;      /* fall through */
    case 6 : b+=k[2];
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 5 : b+=k8[4];                      /* fall through */
    case 4 : a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 3 : a+=((uint32_t)k8[2])<<16;      /* fall through */
    case 2 : a+=k[0];
             break;
    case 1 : a+=k8[0];
             break;
    case 0 : return NON_ZERO_32(c);         /* zero length requires no mixing */
    }

  } else {                        /* need to read the key one byte at a time */
    const uint8_t *k = (const uint8_t *)key;

    /*--------------- all but the last block: affect some 32 bits of (a,b,c) */
    while (length > 12)
    {
      a += k[0];
      a += ((uint32_t)k[1])<<8;
      a += ((uint32_t)k[2])<<16;
      a += ((uint32_t)k[3])<<24;
      b += k[4];
      b += ((uint32_t)k[5])<<8;
      b += ((uint32_t)k[6])<<16;
      b += ((uint32_t)k[7])<<24;
      c += k[8];
      c += ((uint32_t)k[9])<<8;
      c += ((uint32_t)k[10])<<16;
      c += ((uint32_t)k[11])<<24;
      mix(a,b,c);
      length -= 12;
      k += 12;
    }

    /*-------------------------------- last block: affect all 32 bits of (c) */
    switch(length)                   /* all the case statements fall through */
    {
    case 12: c+=((uint32_t)k[11])<<24;
	     /* FALLTHROUGH */
    case 11: c+=((uint32_t)k[10])<<16;
	     /* FALLTHROUGH */
    case 10: c+=((uint32_t)k[9])<<8;
	     /* FALLTHROUGH */
    case 9 : c+=k[8];
	     /* FALLTHROUGH */
    case 8 : b+=((uint32_t)k[7])<<24;
	     /* FALLTHROUGH */
    case 7 : b+=((uint32_t)k[6])<<16;
	     /* FALLTHROUGH */
    case 6 : b+=((uint32_t)k[5])<<8;
	     /* FALLTHROUGH */
    case 5 : b+=k[4];
	     /* FALLTHROUGH */
    case 4 : a+=((uint32_t)k[3])<<24;
	     /* FALLTHROUGH */
    case 3 : a+=((uint32_t)k[2])<<16;
	     /* FALLTHROUGH */
    case 2 : a+=((uint32_t)k[1])<<8;
	     /* FALLTHROUGH */
    case 1 : a+=k[0];
             break;
    case 0 : return NON_ZERO_32(c);
    }
  }

  final(a,b,c);
  return NON_ZERO_32(c);
}

#if SIZEOF_INT64 >= 8
/*
 * hashlittle2: return 2 32-bit hash values joined into an int64.
 *
 * This is identical to hashlittle(), except it returns two 32-bit hash
 * values instead of just one.  This is good enough for hash table
 * lookup with 2^^64 buckets, or if you want a second hash if you're not
 * happy with the first, or if you want a probably-unique 64-bit ID for
 * the key.  *pc is better mixed than *pb, so use *pc first.  If you want
 * a 64-bit value do something like "*pc + (((uint64_t)*pb)<<32)".
 */
int64 hashlittle2(const void *key, size_t length)
{
  uint32_t a,b,c;                                          /* internal state */
  union { const void *ptr; size_t i; } u;     /* needed for Mac Powerbook G4 */

  /* Set up the internal state */
  a = b = c = 0xdeadbeef + ((uint32_t)length);

  u.ptr = key;
  if (HASH_LITTLE_ENDIAN && ((u.i & 0x3) == 0)) {
    const uint32_t *k = (const uint32_t *)key;         /* read 32-bit chunks */
    const uint8_t  *k8;

    /*------ all but last block: aligned reads and affect 32 bits of (a,b,c) */
    while (length > 12)
    {
      a += k[0];
      b += k[1];
      c += k[2];
      mix(a,b,c);
      length -= 12;
      k += 3;
    }

    /*----------------------------- handle the last (probably partial) block */
    k8 = (const uint8_t *)k;
    switch(length)
    {
    case 12: c+=k[2]; b+=k[1]; a+=k[0]; break;
    case 11: c+=((uint32_t)k8[10])<<16;  /* fall through */
    case 10: c+=((uint32_t)k8[9])<<8;    /* fall through */
    case 9 : c+=k8[8];                   /* fall through */
    case 8 : b+=k[1]; a+=k[0]; break;
    case 7 : b+=((uint32_t)k8[6])<<16;   /* fall through */
    case 6 : b+=((uint32_t)k8[5])<<8;    /* fall through */
    case 5 : b+=k8[4];                   /* fall through */
    case 4 : a+=k[0]; break;
    case 3 : a+=((uint32_t)k8[2])<<16;   /* fall through */
    case 2 : a+=((uint32_t)k8[1])<<8;    /* fall through */
    case 1 : a+=k8[0]; break;
    case 0 : return NON_ZERO_64(b, c);
    }
  } else if (HASH_LITTLE_ENDIAN && ((u.i & 0x1) == 0)) {
    const uint16_t *k = (const uint16_t *)key;         /* read 16-bit chunks */
    const uint8_t  *k8;

    /*--------------- all but last block: aligned reads and different mixing */
    while (length > 12)
    {
      a += k[0] + (((uint32_t)k[1])<<16);
      b += k[2] + (((uint32_t)k[3])<<16);
      c += k[4] + (((uint32_t)k[5])<<16);
      mix(a,b,c);
      length -= 12;
      k += 6;
    }

    /*----------------------------- handle the last (probably partial) block */
    k8 = (const uint8_t *)k;
    switch(length)
    {
    case 12: c+=k[4]+(((uint32_t)k[5])<<16);
             b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 11: c+=((uint32_t)k8[10])<<16;     /* fall through */
    case 10: c+=k[4];
             b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 9 : c+=k8[8];                      /* fall through */
    case 8 : b+=k[2]+(((uint32_t)k[3])<<16);
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 7 : b+=((uint32_t)k8[6])<<16;      /* fall through */
    case 6 : b+=k[2];
             a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 5 : b+=k8[4];                      /* fall through */
    case 4 : a+=k[0]+(((uint32_t)k[1])<<16);
             break;
    case 3 : a+=((uint32_t)k8[2])<<16;      /* fall through */
    case 2 : a+=k[0];
             break;
    case 1 : a+=k8[0];
             break;
    case 0 : return NON_ZERO_64(b, c);  /* zero length strings require no mixing */
    }

  } else {                        /* need to read the key one byte at a time */
    const uint8_t *k = (const uint8_t *)key;

    /*--------------- all but the last block: affect some 32 bits of (a,b,c) */
    while (length > 12)
    {
      a += k[0];
      a += ((uint32_t)k[1])<<8;
      a += ((uint32_t)k[2])<<16;
      a += ((uint32_t)k[3])<<24;
      b += k[4];
      b += ((uint32_t)k[5])<<8;
      b += ((uint32_t)k[6])<<16;
      b += ((uint32_t)k[7])<<24;
      c += k[8];
      c += ((uint32_t)k[9])<<8;
      c += ((uint32_t)k[10])<<16;
      c += ((uint32_t)k[11])<<24;
      mix(a,b,c);
      length -= 12;
      k += 12;
    }

    /*-------------------------------- last block: affect all 32 bits of (c) */
    switch(length)                   /* all the case statements fall through */
    {
    case 12: c+=((uint32_t)k[11])<<24;
	     /* FALLTHROUGH */
    case 11: c+=((uint32_t)k[10])<<16;
	     /* FALLTHROUGH */
    case 10: c+=((uint32_t)k[9])<<8;
	     /* FALLTHROUGH */
    case 9 : c+=k[8];
	     /* FALLTHROUGH */
    case 8 : b+=((uint32_t)k[7])<<24;
	     /* FALLTHROUGH */
    case 7 : b+=((uint32_t)k[6])<<16;
	     /* FALLTHROUGH */
    case 6 : b+=((uint32_t)k[5])<<8;
	     /* FALLTHROUGH */
    case 5 : b+=k[4];
	     /* FALLTHROUGH */
    case 4 : a+=((uint32_t)k[3])<<24;
	     /* FALLTHROUGH */
    case 3 : a+=((uint32_t)k[2])<<16;
	     /* FALLTHROUGH */
    case 2 : a+=((uint32_t)k[1])<<8;
	     /* FALLTHROUGH */
    case 1 : a+=k[0];
             break;
    case 0 : return NON_ZERO_64(b, c);
    }
  }

  final(a,b,c);
  return NON_ZERO_64(b, c);
}
#else
#define hashlittle2(key, len) hashlittle(key, len)
#endif
