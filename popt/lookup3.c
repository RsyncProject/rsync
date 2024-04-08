/* -------------------------------------------------------------------- */
/*
 * lookup3.c, by Bob Jenkins, May 2006, Public Domain.
 * 
 * These are functions for producing 32-bit hashes for hash table lookup.
 * jlu32w(), jlu32l(), jlu32lpair(), jlu32b(), _JLU3_MIX(), and _JLU3_FINAL() 
 * are externally useful functions.  Routines to test the hash are included 
 * if SELF_TEST is defined.  You can use this free for any purpose.  It's in
 * the public domain.  It has no warranty.
 * 
 * You probably want to use jlu32l().  jlu32l() and jlu32b()
 * hash byte arrays.  jlu32l() is is faster than jlu32b() on
 * little-endian machines.  Intel and AMD are little-endian machines.
 * On second thought, you probably want jlu32lpair(), which is identical to
 * jlu32l() except it returns two 32-bit hashes for the price of one.  
 * You could implement jlu32bpair() if you wanted but I haven't bothered here.
 * 
 * If you want to find a hash of, say, exactly 7 integers, do
 *   a = i1;  b = i2;  c = i3;
 *   _JLU3_MIX(a,b,c);
 *   a += i4; b += i5; c += i6;
 *   _JLU3_MIX(a,b,c);
 *   a += i7;
 *   _JLU3_FINAL(a,b,c);
 * then use c as the hash value.  If you have a variable size array of
 * 4-byte integers to hash, use jlu32w().  If you have a byte array (like
 * a character string), use jlu32l().  If you have several byte arrays, or
 * a mix of things, see the comments above jlu32l().  
 * 
 * Why is this so big?  I read 12 bytes at a time into 3 4-byte integers, 
 * then mix those integers.  This is fast (you can do a lot more thorough
 * mixing with 12*3 instructions on 3 integers than you can with 3 instructions
 * on 1 byte), but shoehorning those bytes into integers efficiently is messy.
*/
/* -------------------------------------------------------------------- */

#include <stdint.h>

#if defined(_JLU3_SELFTEST)
# define _JLU3_jlu32w		1
# define _JLU3_jlu32l		1
# define _JLU3_jlu32lpair	1
# define _JLU3_jlu32b		1
#endif

static const union _dbswap {
    const uint32_t ui;
    const unsigned char uc[4];
} endian = { .ui = 0x11223344 };
# define HASH_LITTLE_ENDIAN	(endian.uc[0] == (unsigned char) 0x44)
# define HASH_BIG_ENDIAN	(endian.uc[0] == (unsigned char) 0x11)

#ifndef ROTL32
# define ROTL32(x, s) (((x) << (s)) | ((x) >> (32 - (s))))
#endif

/* NOTE: The _size parameter should be in bytes. */
#define	_JLU3_INIT(_h, _size)	(0xdeadbeef + ((uint32_t)(_size)) + (_h))

/* -------------------------------------------------------------------- */
/*
 * _JLU3_MIX -- mix 3 32-bit values reversibly.
 * 
 * This is reversible, so any information in (a,b,c) before _JLU3_MIX() is
 * still in (a,b,c) after _JLU3_MIX().
 * 
 * If four pairs of (a,b,c) inputs are run through _JLU3_MIX(), or through
 * _JLU3_MIX() in reverse, there are at least 32 bits of the output that
 * are sometimes the same for one pair and different for another pair.
 * This was tested for:
 * * pairs that differed by one bit, by two bits, in any combination
 *   of top bits of (a,b,c), or in any combination of bottom bits of
 *   (a,b,c).
 * * "differ" is defined as +, -, ^, or ~^.  For + and -, I transformed
 *   the output delta to a Gray code (a^(a>>1)) so a string of 1's (as
 *   is commonly produced by subtraction) look like a single 1-bit
 *   difference.
 * * the base values were pseudorandom, all zero but one bit set, or 
 *   all zero plus a counter that starts at zero.
 * 
 * Some k values for my "a-=c; a^=ROTL32(c,k); c+=b;" arrangement that
 * satisfy this are
 *     4  6  8 16 19  4
 *     9 15  3 18 27 15
 *    14  9  3  7 17  3
 * Well, "9 15 3 18 27 15" didn't quite get 32 bits diffing
 * for "differ" defined as + with a one-bit base and a two-bit delta.  I
 * used http://burtleburtle.net/bob/hash/avalanche.html to choose 
 * the operations, constants, and arrangements of the variables.
 * 
 * This does not achieve avalanche.  There are input bits of (a,b,c)
 * that fail to affect some output bits of (a,b,c), especially of a.  The
 * most thoroughly mixed value is c, but it doesn't really even achieve
 * avalanche in c.
 * 
 * This allows some parallelism.  Read-after-writes are good at doubling
 * the number of bits affected, so the goal of mixing pulls in the opposite
 * direction as the goal of parallelism.  I did what I could.  Rotates
 * seem to cost as much as shifts on every machine I could lay my hands
 * on, and rotates are much kinder to the top and bottom bits, so I used
 * rotates.
 */
/* -------------------------------------------------------------------- */
#define _JLU3_MIX(a,b,c) \
{ \
  a -= c;  a ^= ROTL32(c, 4);  c += b; \
  b -= a;  b ^= ROTL32(a, 6);  a += c; \
  c -= b;  c ^= ROTL32(b, 8);  b += a; \
  a -= c;  a ^= ROTL32(c,16);  c += b; \
  b -= a;  b ^= ROTL32(a,19);  a += c; \
  c -= b;  c ^= ROTL32(b, 4);  b += a; \
}

/* -------------------------------------------------------------------- */
/**
 * _JLU3_FINAL -- final mixing of 3 32-bit values (a,b,c) into c
 * 
 * Pairs of (a,b,c) values differing in only a few bits will usually
 * produce values of c that look totally different.  This was tested for
 * * pairs that differed by one bit, by two bits, in any combination
 *   of top bits of (a,b,c), or in any combination of bottom bits of
 *   (a,b,c).
 * * "differ" is defined as +, -, ^, or ~^.  For + and -, I transformed
 *   the output delta to a Gray code (a^(a>>1)) so a string of 1's (as
 *   is commonly produced by subtraction) look like a single 1-bit
 *   difference.
 * * the base values were pseudorandom, all zero but one bit set, or 
 *   all zero plus a counter that starts at zero.
 * 
 * These constants passed:
 *  14 11 25 16 4 14 24
 *  12 14 25 16 4 14 24
 * and these came close:
 *   4  8 15 26 3 22 24
 *  10  8 15 26 3 22 24
 *  11  8 15 26 3 22 24
 */
/* -------------------------------------------------------------------- */
#define _JLU3_FINAL(a,b,c) \
{ \
  c ^= b; c -= ROTL32(b,14); \
  a ^= c; a -= ROTL32(c,11); \
  b ^= a; b -= ROTL32(a,25); \
  c ^= b; c -= ROTL32(b,16); \
  a ^= c; a -= ROTL32(c,4);  \
  b ^= a; b -= ROTL32(a,14); \
  c ^= b; c -= ROTL32(b,24); \
}

#if defined(_JLU3_jlu32w)
uint32_t jlu32w(uint32_t h, const uint32_t *k, size_t size);
/* -------------------------------------------------------------------- */
/**
 *  This works on all machines.  To be useful, it requires
 *  -- that the key be an array of uint32_t's, and
 *  -- that the size be the number of uint32_t's in the key
 * 
 *  The function jlu32w() is identical to jlu32l() on little-endian
 *  machines, and identical to jlu32b() on big-endian machines,
 *  except that the size has to be measured in uint32_ts rather than in
 *  bytes.  jlu32l() is more complicated than jlu32w() only because
 *  jlu32l() has to dance around fitting the key bytes into registers.
 *
 * @param h		the previous hash, or an arbitrary value
 * @param *k		the key, an array of uint32_t values
 * @param size		the size of the key, in uint32_ts
 * @return		the lookup3 hash
 */
/* -------------------------------------------------------------------- */
uint32_t jlu32w(uint32_t h, const uint32_t *k, size_t size)
{
    uint32_t a = _JLU3_INIT(h, (size * sizeof(*k)));
    uint32_t b = a;
    uint32_t c = a;

    if (k == NULL)
	goto exit;

    /*----------------------------------------------- handle most of the key */
    while (size > 3) {
	a += k[0];
	b += k[1];
	c += k[2];
	_JLU3_MIX(a,b,c);
	size -= 3;
	k += 3;
    }

    /*----------------------------------------- handle the last 3 uint32_t's */
    switch (size) {
    case 3 : c+=k[2];
    case 2 : b+=k[1];
    case 1 : a+=k[0];
	_JLU3_FINAL(a,b,c);
	/* fallthrough */
    case 0:
	break;
    }
    /*---------------------------------------------------- report the result */
exit:
    return c;
}
#endif	/* defined(_JLU3_jlu32w) */

#if defined(_JLU3_jlu32l)
uint32_t jlu32l(uint32_t h, const void *key, size_t size);
/* -------------------------------------------------------------------- */
/*
 * jlu32l() -- hash a variable-length key into a 32-bit value
 *   h       : can be any 4-byte value
 *   k       : the key (the unaligned variable-length array of bytes)
 *   size    : the size of the key, counting by bytes
 * Returns a 32-bit value.  Every bit of the key affects every bit of
 * the return value.  Two keys differing by one or two bits will have
 * totally different hash values.
 * 
 * The best hash table sizes are powers of 2.  There is no need to do
 * mod a prime (mod is sooo slow!).  If you need less than 32 bits,
 * use a bitmask.  For example, if you need only 10 bits, do
 *   h = (h & hashmask(10));
 * In which case, the hash table should have hashsize(10) elements.
 * 
 * If you are hashing n strings (uint8_t **)k, do it like this:
 *   for (i=0, h=0; i<n; ++i) h = jlu32l(h, k[i], len[i]);
 * 
 * By Bob Jenkins, 2006.  bob_jenkins@burtleburtle.net.  You may use this
 * code any way you wish, private, educational, or commercial.  It's free.
 * 
 * Use for hash table lookup, or anything where one collision in 2^^32 is
 * acceptable.  Do NOT use for cryptographic purposes.
 *
 * @param h		the previous hash, or an arbitrary value
 * @param *k		the key, an array of uint8_t values
 * @param size		the size of the key
 * @return		the lookup3 hash
 */
/* -------------------------------------------------------------------- */
uint32_t jlu32l(uint32_t h, const void *key, size_t size)
{
    union { const void *ptr; size_t i; } u;
    uint32_t a = _JLU3_INIT(h, size);
    uint32_t b = a;
    uint32_t c = a;

    if (key == NULL)
	goto exit;

    u.ptr = key;
    if (HASH_LITTLE_ENDIAN && ((u.i & 0x3) == 0)) {
	const uint32_t *k = (const uint32_t *)key;	/* read 32-bit chunks */
#ifdef	VALGRIND
	const uint8_t  *k8;
#endif

    /*------ all but last block: aligned reads and affect 32 bits of (a,b,c) */
	while (size > 12) {
	    a += k[0];
	    b += k[1];
	    c += k[2];
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 3;
	}

	/*------------------------- handle the last (probably partial) block */
	/* 
	 * "k[2]&0xffffff" actually reads beyond the end of the string, but
	 * then masks off the part it's not allowed to read.  Because the
	 * string is aligned, the masked-off tail is in the same word as the
	 * rest of the string.  Every machine with memory protection I've seen
	 * does it on word boundaries, so is OK with this.  But VALGRIND will
	 * still catch it and complain.  The masking trick does make the hash
	 * noticeably faster for short strings (like English words).
	 */
#ifndef VALGRIND

	switch (size) {
	case 12:	c += k[2]; b+=k[1]; a+=k[0]; break;
	case 11:	c += k[2]&0xffffff; b+=k[1]; a+=k[0]; break;
	case 10:	c += k[2]&0xffff; b+=k[1]; a+=k[0]; break;
	case  9:	c += k[2]&0xff; b+=k[1]; a+=k[0]; break;
	case  8:	b += k[1]; a+=k[0]; break;
	case  7:	b += k[1]&0xffffff; a+=k[0]; break;
	case  6:	b += k[1]&0xffff; a+=k[0]; break;
	case  5:	b += k[1]&0xff; a+=k[0]; break;
	case  4:	a += k[0]; break;
	case  3:	a += k[0]&0xffffff; break;
	case  2:	a += k[0]&0xffff; break;
	case  1:	a += k[0]&0xff; break;
	case  0:	goto exit;
	}

#else /* make valgrind happy */

	k8 = (const uint8_t *)k;
	switch (size) {
	case 12:	c += k[2]; b+=k[1]; a+=k[0]	break;
	case 11:	c += ((uint32_t)k8[10])<<16;	/* fallthrough */
	case 10:	c += ((uint32_t)k8[9])<<8;	/* fallthrough */
	case  9:	c += k8[8];			/* fallthrough */
	case  8:	b += k[1]; a+=k[0];		break;
	case  7:	b += ((uint32_t)k8[6])<<16;	/* fallthrough */
	case  6:	b += ((uint32_t)k8[5])<<8;	/* fallthrough */
	case  5:	b += k8[4];			/* fallthrough */
	case  4:	a += k[0];			break;
	case  3:	a += ((uint32_t)k8[2])<<16;	/* fallthrough */
	case  2:	a += ((uint32_t)k8[1])<<8;	/* fallthrough */
	case  1:	a += k8[0];			break;
	case  0:	goto exit;
	}

#endif /* !valgrind */

    } else if (HASH_LITTLE_ENDIAN && ((u.i & 0x1) == 0)) {
	const uint16_t *k = (const uint16_t *)key;	/* read 16-bit chunks */
	const uint8_t  *k8;

	/*----------- all but last block: aligned reads and different mixing */
	while (size > 12) {
	    a += k[0] + (((uint32_t)k[1])<<16);
	    b += k[2] + (((uint32_t)k[3])<<16);
	    c += k[4] + (((uint32_t)k[5])<<16);
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 6;
	}

	/*------------------------- handle the last (probably partial) block */
	k8 = (const uint8_t *)k;
	switch (size) {
	case 12:
	    c += k[4]+(((uint32_t)k[5])<<16);
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case 11:
	    c += ((uint32_t)k8[10])<<16;
	    /* fallthrough */
	case 10:
	    c += (uint32_t)k[4];
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  9:
	    c += (uint32_t)k8[8];
	    /* fallthrough */
	case  8:
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  7:
	    b += ((uint32_t)k8[6])<<16;
	    /* fallthrough */
	case  6:
	    b += (uint32_t)k[2];
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  5:
	    b += (uint32_t)k8[4];
	    /* fallthrough */
	case  4:
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  3:
	    a += ((uint32_t)k8[2])<<16;
	    /* fallthrough */
	case  2:
	    a += (uint32_t)k[0];
	    break;
	case  1:
	    a += (uint32_t)k8[0];
	    break;
	case  0:
	    goto exit;
	}

    } else {		/* need to read the key one byte at a time */
	const uint8_t *k = (const uint8_t *)key;

	/*----------- all but the last block: affect some 32 bits of (a,b,c) */
	while (size > 12) {
	    a += (uint32_t)k[0];
	    a += ((uint32_t)k[1])<<8;
	    a += ((uint32_t)k[2])<<16;
	    a += ((uint32_t)k[3])<<24;
	    b += (uint32_t)k[4];
	    b += ((uint32_t)k[5])<<8;
	    b += ((uint32_t)k[6])<<16;
	    b += ((uint32_t)k[7])<<24;
	    c += (uint32_t)k[8];
	    c += ((uint32_t)k[9])<<8;
	    c += ((uint32_t)k[10])<<16;
	    c += ((uint32_t)k[11])<<24;
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 12;
	}

	/*---------------------------- last block: affect all 32 bits of (c) */
	switch (size) {
	case 12:	c += ((uint32_t)k[11])<<24;	/* fallthrough */
	case 11:	c += ((uint32_t)k[10])<<16;	/* fallthrough */
	case 10:	c += ((uint32_t)k[9])<<8;	/* fallthrough */
	case  9:	c += (uint32_t)k[8];		/* fallthrough */
	case  8:	b += ((uint32_t)k[7])<<24;	/* fallthrough */
	case  7:	b += ((uint32_t)k[6])<<16;	/* fallthrough */
	case  6:	b += ((uint32_t)k[5])<<8;	/* fallthrough */
	case  5:	b += (uint32_t)k[4];		/* fallthrough */
	case  4:	a += ((uint32_t)k[3])<<24;	/* fallthrough */
	case  3:	a += ((uint32_t)k[2])<<16;	/* fallthrough */
	case  2:	a += ((uint32_t)k[1])<<8;	/* fallthrough */
	case  1:	a += (uint32_t)k[0];
	    break;
	case  0:
	    goto exit;
	}
    }

    _JLU3_FINAL(a,b,c);

exit:
    return c;
}
#endif	/* defined(_JLU3_jlu32l) */

#if defined(_JLU3_jlu32lpair)
/**
 * jlu32lpair: return 2 32-bit hash values.
 *
 * This is identical to jlu32l(), except it returns two 32-bit hash
 * values instead of just one.  This is good enough for hash table
 * lookup with 2^^64 buckets, or if you want a second hash if you're not
 * happy with the first, or if you want a probably-unique 64-bit ID for
 * the key.  *pc is better mixed than *pb, so use *pc first.  If you want
 * a 64-bit value do something like "*pc + (((uint64_t)*pb)<<32)".
 *
 * @param h		the previous hash, or an arbitrary value
 * @param *key		the key, an array of uint8_t values
 * @param size		the size of the key in bytes
 * @retval *pc,		IN: primary initval, OUT: primary hash
 * *retval *pb		IN: secondary initval, OUT: secondary hash
 */
void jlu32lpair(const void *key, size_t size, uint32_t *pc, uint32_t *pb)
{
    union { const void *ptr; size_t i; } u;
    uint32_t a = _JLU3_INIT(*pc, size);
    uint32_t b = a;
    uint32_t c = a;

    if (key == NULL)
	goto exit;

    c += *pb;	/* Add the secondary hash. */

    u.ptr = key;
    if (HASH_LITTLE_ENDIAN && ((u.i & 0x3) == 0)) {
	const uint32_t *k = (const uint32_t *)key;	/* read 32-bit chunks */
#ifdef	VALGRIND
	const uint8_t  *k8;
#endif

	/*-- all but last block: aligned reads and affect 32 bits of (a,b,c) */
	while (size > (size_t)12) {
	    a += k[0];
	    b += k[1];
	    c += k[2];
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 3;
	}
	/*------------------------- handle the last (probably partial) block */
	/* 
	 * "k[2]&0xffffff" actually reads beyond the end of the string, but
	 * then masks off the part it's not allowed to read.  Because the
	 * string is aligned, the masked-off tail is in the same word as the
	 * rest of the string.  Every machine with memory protection I've seen
	 * does it on word boundaries, so is OK with this.  But VALGRIND will
	 * still catch it and complain.  The masking trick does make the hash
	 * noticeably faster for short strings (like English words).
	 */
#ifndef VALGRIND

	switch (size) {
	case 12:	c += k[2]; b+=k[1]; a+=k[0]; break;
	case 11:	c += k[2]&0xffffff; b+=k[1]; a+=k[0]; break;
	case 10:	c += k[2]&0xffff; b+=k[1]; a+=k[0]; break;
	case  9:	c += k[2]&0xff; b+=k[1]; a+=k[0]; break;
	case  8:	b += k[1]; a+=k[0]; break;
	case  7:	b += k[1]&0xffffff; a+=k[0]; break;
	case  6:	b += k[1]&0xffff; a+=k[0]; break;
	case  5:	b += k[1]&0xff; a+=k[0]; break;
	case  4:	a += k[0]; break;
	case  3:	a += k[0]&0xffffff; break;
	case  2:	a += k[0]&0xffff; break;
	case  1:	a += k[0]&0xff; break;
	case  0:	goto exit;
	}

#else /* make valgrind happy */

	k8 = (const uint8_t *)k;
	switch (size) {
	case 12:	c += k[2]; b+=k[1]; a+=k[0];	break;
	case 11:	c += ((uint32_t)k8[10])<<16;	/* fallthrough */
	case 10:	c += ((uint32_t)k8[9])<<8;	/* fallthrough */
	case  9:	c += k8[8];			/* fallthrough */
	case  8:	b += k[1]; a+=k[0];		break;
	case  7:	b += ((uint32_t)k8[6])<<16;	/* fallthrough */
	case  6:	b += ((uint32_t)k8[5])<<8;	/* fallthrough */
	case  5:	b += k8[4];			/* fallthrough */
	case  4:	a += k[0];			break;
	case  3:	a += ((uint32_t)k8[2])<<16;	/* fallthrough */
	case  2:	a += ((uint32_t)k8[1])<<8;	/* fallthrough */
	case  1:	a += k8[0];			break;
	case  0:	goto exit;
	}

#endif /* !valgrind */

    } else if (HASH_LITTLE_ENDIAN && ((u.i & 0x1) == 0)) {
	const uint16_t *k = (const uint16_t *)key;	/* read 16-bit chunks */
	const uint8_t  *k8;

	/*----------- all but last block: aligned reads and different mixing */
	while (size > (size_t)12) {
	    a += k[0] + (((uint32_t)k[1])<<16);
	    b += k[2] + (((uint32_t)k[3])<<16);
	    c += k[4] + (((uint32_t)k[5])<<16);
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 6;
	}

	/*------------------------- handle the last (probably partial) block */
	k8 = (const uint8_t *)k;
	switch (size) {
	case 12:
	    c += k[4]+(((uint32_t)k[5])<<16);
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case 11:
	    c += ((uint32_t)k8[10])<<16;
	    /* fallthrough */
	case 10:
	    c += k[4];
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  9:
	    c += k8[8];
	    /* fallthrough */
	case  8:
	    b += k[2]+(((uint32_t)k[3])<<16);
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  7:
	    b += ((uint32_t)k8[6])<<16;
	    /* fallthrough */
	case  6:
	    b += k[2];
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  5:
	    b += k8[4];
	    /* fallthrough */
	case  4:
	    a += k[0]+(((uint32_t)k[1])<<16);
	    break;
	case  3:
	    a += ((uint32_t)k8[2])<<16;
	    /* fallthrough */
	case  2:
	    a += k[0];
	    break;
	case  1:
	    a += k8[0];
	    break;
	case  0:
	    goto exit;
	}

    } else {		/* need to read the key one byte at a time */
	const uint8_t *k = (const uint8_t *)key;

	/*----------- all but the last block: affect some 32 bits of (a,b,c) */
	while (size > (size_t)12) {
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
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 12;
	}

	/*---------------------------- last block: affect all 32 bits of (c) */
	switch (size) {
	case 12:	c += ((uint32_t)k[11])<<24;	/* fallthrough */
	case 11:	c += ((uint32_t)k[10])<<16;	/* fallthrough */
	case 10:	c += ((uint32_t)k[9])<<8;	/* fallthrough */
	case  9:	c += k[8];			/* fallthrough */
	case  8:	b += ((uint32_t)k[7])<<24;	/* fallthrough */
	case  7:	b += ((uint32_t)k[6])<<16;	/* fallthrough */
	case  6:	b += ((uint32_t)k[5])<<8;	/* fallthrough */
	case  5:	b += k[4];			/* fallthrough */
	case  4:	a += ((uint32_t)k[3])<<24;	/* fallthrough */
	case  3:	a += ((uint32_t)k[2])<<16;	/* fallthrough */
	case  2:	a += ((uint32_t)k[1])<<8;	/* fallthrough */
	case  1:	a += k[0];
	    break;
	case  0:
	    goto exit;
	}
    }

    _JLU3_FINAL(a,b,c);

exit:
    *pc = c;
    *pb = b;
    return;
}
#endif	/* defined(_JLU3_jlu32lpair) */

#if defined(_JLU3_jlu32b)
uint32_t jlu32b(uint32_t h, const void *key, size_t size);
/*
 * jlu32b():
 * This is the same as jlu32w() on big-endian machines.  It is different
 * from jlu32l() on all machines.  jlu32b() takes advantage of
 * big-endian byte ordering. 
 *
 * @param h		the previous hash, or an arbitrary value
 * @param *k		the key, an array of uint8_t values
 * @param size		the size of the key
 * @return		the lookup3 hash
 */
uint32_t jlu32b(uint32_t h, const void *key, size_t size)
{
    union { const void *ptr; size_t i; } u;
    uint32_t a = _JLU3_INIT(h, size);
    uint32_t b = a;
    uint32_t c = a;

    if (key == NULL)
	return h;

    u.ptr = key;
    if (HASH_BIG_ENDIAN && ((u.i & 0x3) == 0)) {
	const uint32_t *k = (const uint32_t *)key;	/* read 32-bit chunks */
#ifdef	VALGRIND
	const uint8_t  *k8;
#endif

	/*-- all but last block: aligned reads and affect 32 bits of (a,b,c) */
	while (size > 12) {
	    a += k[0];
	    b += k[1];
	    c += k[2];
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 3;
	}

	/*------------------------- handle the last (probably partial) block */
	/* 
	 * "k[2]<<8" actually reads beyond the end of the string, but
	 * then shifts out the part it's not allowed to read.  Because the
	 * string is aligned, the illegal read is in the same word as the
	 * rest of the string.  Every machine with memory protection I've seen
	 * does it on word boundaries, so is OK with this.  But VALGRIND will
	 * still catch it and complain.  The masking trick does make the hash
	 * noticeably faster for short strings (like English words).
	 */
#ifndef VALGRIND

	switch (size) {
	case 12:	c += k[2]; b+=k[1]; a+=k[0]; break;
	case 11:	c += k[2]&0xffffff00; b+=k[1]; a+=k[0]; break;
	case 10:	c += k[2]&0xffff0000; b+=k[1]; a+=k[0]; break;
	case  9:	c += k[2]&0xff000000; b+=k[1]; a+=k[0]; break;
	case  8:	b += k[1]; a+=k[0]; break;
	case  7:	b += k[1]&0xffffff00; a+=k[0]; break;
	case  6:	b += k[1]&0xffff0000; a+=k[0]; break;
	case  5:	b += k[1]&0xff000000; a+=k[0]; break;
	case  4:	a += k[0]; break;
	case  3:	a += k[0]&0xffffff00; break;
	case  2:	a += k[0]&0xffff0000; break;
	case  1:	a += k[0]&0xff000000; break;
	case  0:	goto exit;
    }

#else  /* make valgrind happy */

	k8 = (const uint8_t *)k;
	switch (size) {	/* all the case statements fall through */
	case 12:	c += k[2]; b+=k[1]; a+=k[0];	break;
	case 11:	c += ((uint32_t)k8[10])<<8;	/* fallthrough */
	case 10:	c += ((uint32_t)k8[9])<<16;	/* fallthrough */
	case  9:	c += ((uint32_t)k8[8])<<24;	/* fallthrough */
	case  8:	b += k[1]; a+=k[0];		break;
	case  7:	b += ((uint32_t)k8[6])<<8;	/* fallthrough */
	case  6:	b += ((uint32_t)k8[5])<<16;	/* fallthrough */
	case  5:	b += ((uint32_t)k8[4])<<24;	/* fallthrough */
	case  4:	a += k[0];			break;
	case  3:	a += ((uint32_t)k8[2])<<8;	/* fallthrough */
	case  2:	a += ((uint32_t)k8[1])<<16;	/* fallthrough */
	case  1:	a += ((uint32_t)k8[0])<<24;	break;
	case  0:	goto exit;
    }

#endif /* !VALGRIND */

    } else {                        /* need to read the key one byte at a time */
	const uint8_t *k = (const uint8_t *)key;

	/*----------- all but the last block: affect some 32 bits of (a,b,c) */
	while (size > 12) {
	    a += ((uint32_t)k[0])<<24;
	    a += ((uint32_t)k[1])<<16;
	    a += ((uint32_t)k[2])<<8;
	    a += ((uint32_t)k[3]);
	    b += ((uint32_t)k[4])<<24;
	    b += ((uint32_t)k[5])<<16;
	    b += ((uint32_t)k[6])<<8;
	    b += ((uint32_t)k[7]);
	    c += ((uint32_t)k[8])<<24;
	    c += ((uint32_t)k[9])<<16;
	    c += ((uint32_t)k[10])<<8;
	    c += ((uint32_t)k[11]);
	    _JLU3_MIX(a,b,c);
	    size -= 12;
	    k += 12;
	}

	/*---------------------------- last block: affect all 32 bits of (c) */
	switch (size) {	/* all the case statements fall through */
	case 12:	c += k[11];			/* fallthrough */
	case 11:	c += ((uint32_t)k[10])<<8;	/* fallthrough */
	case 10:	c += ((uint32_t)k[9])<<16;	/* fallthrough */
	case  9:	c += ((uint32_t)k[8])<<24;	/* fallthrough */
	case  8:	b += k[7];			/* fallthrough */
	case  7:	b += ((uint32_t)k[6])<<8;	/* fallthrough */
	case  6:	b += ((uint32_t)k[5])<<16;	/* fallthrough */
	case  5:	b += ((uint32_t)k[4])<<24;	/* fallthrough */
	case  4:	a += k[3];			/* fallthrough */
	case  3:	a += ((uint32_t)k[2])<<8;	/* fallthrough */
	case  2:	a += ((uint32_t)k[1])<<16;	/* fallthrough */
	case  1:	a += ((uint32_t)k[0])<<24;	/* fallthrough */
	    break;
	case  0:
	    goto exit;
	}
    }

    _JLU3_FINAL(a,b,c);

exit:
    return c;
}
#endif	/* defined(_JLU3_jlu32b) */

#if defined(_JLU3_SELFTEST)

/* used for timings */
static void driver1(void)
{
    uint8_t buf[256];
    uint32_t i;
    uint32_t h=0;
    time_t a,z;

    time(&a);
    for (i=0; i<256; ++i) buf[i] = 'x';
    for (i=0; i<1; ++i) {
	h = jlu32l(h, &buf[0], sizeof(buf[0]));
    }
    time(&z);
    if (z-a > 0) printf("time %d %.8x\n", (int)(z-a), h);
}

/* check that every input bit changes every output bit half the time */
#define HASHSTATE 1
#define HASHLEN   1
#define MAXPAIR 60
#define MAXLEN  70
static void driver2(void)
{
    uint8_t qa[MAXLEN+1], qb[MAXLEN+2], *a = &qa[0], *b = &qb[1];
    uint32_t c[HASHSTATE], d[HASHSTATE], i=0, j=0, k, l, m=0, z;
    uint32_t e[HASHSTATE],f[HASHSTATE],g[HASHSTATE],h[HASHSTATE];
    uint32_t x[HASHSTATE],y[HASHSTATE];
    uint32_t hlen;

    printf("No more than %d trials should ever be needed \n",MAXPAIR/2);
    for (hlen=0; hlen < MAXLEN; ++hlen) {
	z=0;
	for (i=0; i<hlen; ++i) {	/*-------------- for each input byte, */
	    for (j=0; j<8; ++j) {	/*--------------- for each input bit, */
		for (m=1; m<8; ++m) {	/*---- for several possible initvals, */
		    for (l=0; l<HASHSTATE; ++l)
			e[l]=f[l]=g[l]=h[l]=x[l]=y[l]=~((uint32_t)0);

		    /* check that every output bit is affected by that input bit */
		    for (k=0; k<MAXPAIR; k+=2) { 
			uint32_t finished=1;
			/* keys have one bit different */
			for (l=0; l<hlen+1; ++l) {a[l] = b[l] = (uint8_t)0;}
			/* have a and b be two keys differing in only one bit */
			a[i] ^= (k<<j);
			a[i] ^= (k>>(8-j));
			c[0] = jlu32l(m, a, hlen);
			b[i] ^= ((k+1)<<j);
			b[i] ^= ((k+1)>>(8-j));
			d[0] = jlu32l(m, b, hlen);
			/* check every bit is 1, 0, set, and not set at least once */
			for (l=0; l<HASHSTATE; ++l) {
			    e[l] &= (c[l]^d[l]);
			    f[l] &= ~(c[l]^d[l]);
			    g[l] &= c[l];
			    h[l] &= ~c[l];
			    x[l] &= d[l];
			    y[l] &= ~d[l];
			    if (e[l]|f[l]|g[l]|h[l]|x[l]|y[l]) finished=0;
			}
			if (finished) break;
		    }
		    if (k>z) z=k;
		    if (k == MAXPAIR) {
			printf("Some bit didn't change: ");
			printf("%.8x %.8x %.8x %.8x %.8x %.8x  ",
				e[0],f[0],g[0],h[0],x[0],y[0]);
			printf("i %u j %u m %u len %u\n", i, j, m, hlen);
		    }
		    if (z == MAXPAIR) goto done;
		}
	    }
	}
   done:
	if (z < MAXPAIR) {
	    printf("Mix success  %2u bytes  %2u initvals  ",i,m);
	    printf("required  %u  trials\n", z/2);
	}
    }
    printf("\n");
}

/* Check for reading beyond the end of the buffer and alignment problems */
static void driver3(void)
{
    uint8_t buf[MAXLEN+20], *b;
    uint32_t len;
    uint8_t q[] = "This is the time for all good men to come to the aid of their country...";
    uint32_t h;
    uint8_t qq[] = "xThis is the time for all good men to come to the aid of their country...";
    uint32_t i;
    uint8_t qqq[] = "xxThis is the time for all good men to come to the aid of their country...";
    uint32_t j;
    uint8_t qqqq[] = "xxxThis is the time for all good men to come to the aid of their country...";
    uint32_t ref,x,y;
    uint8_t *p;
    uint32_t m = 13;

    printf("Endianness.  These lines should all be the same (for values filled in):\n");
    printf("%.8x                            %.8x                            %.8x\n",
	jlu32w(m, (const uint32_t *)q, (sizeof(q)-1)/4),
	jlu32w(m, (const uint32_t *)q, (sizeof(q)-5)/4),
	jlu32w(m, (const uint32_t *)q, (sizeof(q)-9)/4));
    p = q;
    printf("%.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x\n",
	jlu32l(m, p, sizeof(q)-1), jlu32l(m, p, sizeof(q)-2),
	jlu32l(m, p, sizeof(q)-3), jlu32l(m, p, sizeof(q)-4),
	jlu32l(m, p, sizeof(q)-5), jlu32l(m, p, sizeof(q)-6),
	jlu32l(m, p, sizeof(q)-7), jlu32l(m, p, sizeof(q)-8),
	jlu32l(m, p, sizeof(q)-9), jlu32l(m, p, sizeof(q)-10),
	jlu32l(m, p, sizeof(q)-11), jlu32l(m, p, sizeof(q)-12));
    p = &qq[1];
    printf("%.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x\n",
	jlu32l(m, p, sizeof(q)-1), jlu32l(m, p, sizeof(q)-2),
	jlu32l(m, p, sizeof(q)-3), jlu32l(m, p, sizeof(q)-4),
	jlu32l(m, p, sizeof(q)-5), jlu32l(m, p, sizeof(q)-6),
	jlu32l(m, p, sizeof(q)-7), jlu32l(m, p, sizeof(q)-8),
	jlu32l(m, p, sizeof(q)-9), jlu32l(m, p, sizeof(q)-10),
	jlu32l(m, p, sizeof(q)-11), jlu32l(m, p, sizeof(q)-12));
    p = &qqq[2];
    printf("%.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x\n",
	jlu32l(m, p, sizeof(q)-1), jlu32l(m, p, sizeof(q)-2),
	jlu32l(m, p, sizeof(q)-3), jlu32l(m, p, sizeof(q)-4),
	jlu32l(m, p, sizeof(q)-5), jlu32l(m, p, sizeof(q)-6),
	jlu32l(m, p, sizeof(q)-7), jlu32l(m, p, sizeof(q)-8),
	jlu32l(m, p, sizeof(q)-9), jlu32l(m, p, sizeof(q)-10),
	jlu32l(m, p, sizeof(q)-11), jlu32l(m, p, sizeof(q)-12));
    p = &qqqq[3];
    printf("%.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x %.8x\n",
	jlu32l(m, p, sizeof(q)-1), jlu32l(m, p, sizeof(q)-2),
	jlu32l(m, p, sizeof(q)-3), jlu32l(m, p, sizeof(q)-4),
	jlu32l(m, p, sizeof(q)-5), jlu32l(m, p, sizeof(q)-6),
	jlu32l(m, p, sizeof(q)-7), jlu32l(m, p, sizeof(q)-8),
	jlu32l(m, p, sizeof(q)-9), jlu32l(m, p, sizeof(q)-10),
	jlu32l(m, p, sizeof(q)-11), jlu32l(m, p, sizeof(q)-12));
    printf("\n");
    for (h=0, b=buf+1; h<8; ++h, ++b) {
	for (i=0; i<MAXLEN; ++i) {
	    len = i;
	    for (j=0; j<i; ++j)
		*(b+j)=0;

	    /* these should all be equal */
	    m = 1;
	    ref = jlu32l(m, b, len);
	    *(b+i)=(uint8_t)~0;
	    *(b-1)=(uint8_t)~0;
	    x = jlu32l(m, b, len);
	    y = jlu32l(m, b, len);
	    if ((ref != x) || (ref != y)) 
		printf("alignment error: %.8x %.8x %.8x %u %u\n",ref,x,y, h, i);
	}
    }
}

/* check for problems with nulls */
static void driver4(void)
{
    uint8_t buf[1];
    uint32_t h;
    uint32_t i;
    uint32_t state[HASHSTATE];

    buf[0] = ~0;
    for (i=0; i<HASHSTATE; ++i)
	state[i] = 1;
    printf("These should all be different\n");
    h = 0;
    for (i=0; i<8; ++i) {
	h = jlu32l(h, buf, 0);
	printf("%2ld  0-byte strings, hash is  %.8x\n", (long)i, h);
    }
}


int main(int argc, char ** argv)
{
    driver1();	/* test that the key is hashed: used for timings */
    driver2();	/* test that whole key is hashed thoroughly */
    driver3();	/* test that nothing but the key is hashed */
    driver4();	/* test hashing multiple buffers (all buffers are null) */
    return 1;
}

#endif  /* _JLU3_SELFTEST */
