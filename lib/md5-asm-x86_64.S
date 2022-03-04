/*
 * x86-64 optimized assembler MD5 implementation
 *
 * Author: Marc Bevand, 2004
 *
 * This code was placed in the public domain by the author. The original
 * publication can be found at:
 *
 * https://www.zorinaq.com/papers/md5-amd64.html
 */
/*
 * No modifications were made aside from changing the function and file names.
 * The MD5_CTX structure as expected here (from OpenSSL) is binary compatible
 * with the md_context used by rsync, for the fields accessed.
 *
 * Benchmarks (in MB/s)            C     ASM
 * - Intel Atom D2700            302     334
 * - Intel i7-7700hq             351     376
 * - AMD ThreadRipper 2950x      728     784
 *
 * The original code was also incorporated into OpenSSL. It has since been
 * modified there. Those changes have not been made here due to licensing
 * incompatibilities. Benchmarks of those changes on the above CPUs did not
 * show any significant difference in performance, though.
 */

#include "config.h"
#include "md-defines.h"

#ifdef USE_MD5_ASM /* { */

#ifdef __APPLE__
#define md5_process_asm _md5_process_asm
#endif

.text
.align 16

.globl md5_process_asm
md5_process_asm:
	push	%rbp
	push	%rbx
	push	%r12
	push	%r13			# not really useful (r13 is unused)
	push	%r14
	push	%r15

	# rdi = arg #1 (ctx, MD5_CTX pointer)
	# rsi = arg #2 (ptr, data pointer)
	# rdx = arg #3 (nbr, number of 16-word blocks to process)
	mov	%rdi,		%rbp	# rbp = ctx
	shl	$6,		%rdx	# rdx = nbr in bytes
	lea	(%rsi,%rdx),	%rdi	# rdi = end
	mov	0*4(%rbp),	%eax	# eax = ctx->A
	mov	1*4(%rbp),	%ebx	# ebx = ctx->B
	mov	2*4(%rbp),	%ecx	# ecx = ctx->C
	mov	3*4(%rbp),	%edx	# edx = ctx->D
	# end is 'rdi'
	# ptr is 'rsi'
	# A is 'eax'
	# B is 'ebx'
	# C is 'ecx'
	# D is 'edx'

	cmp	%rdi,		%rsi		# cmp end with ptr
	je	1f				# jmp if ptr == end

	# BEGIN of loop over 16-word blocks
2:	# save old values of A, B, C, D
	mov	%eax,		%r8d
	mov	%ebx,		%r9d
	mov	%ecx,		%r14d
	mov	%edx,		%r15d
 mov	0*4(%rsi),	%r10d		/* (NEXT STEP) X[0] */
 mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	xor	%ecx,		%r11d		/* y ^ ... */
	lea	-680876936(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r11d		/* x & ... */
	xor	%edx,		%r11d		/* z ^ ... */
	mov	1*4(%rsi),%r10d		/* (NEXT STEP) X[1] */
	add	%r11d,		%eax		/* dst += ... */
	rol	$7,		%eax		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%ebx,		%eax		/* dst += x */
	xor	%ebx,		%r11d		/* y ^ ... */
	lea	-389564586(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r11d		/* x & ... */
	xor	%ecx,		%r11d		/* z ^ ... */
	mov	2*4(%rsi),%r10d		/* (NEXT STEP) X[2] */
	add	%r11d,		%edx		/* dst += ... */
	rol	$12,		%edx		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%eax,		%edx		/* dst += x */
	xor	%eax,		%r11d		/* y ^ ... */
	lea	606105819(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r11d		/* x & ... */
	xor	%ebx,		%r11d		/* z ^ ... */
	mov	3*4(%rsi),%r10d		/* (NEXT STEP) X[3] */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$17,		%ecx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%edx,		%ecx		/* dst += x */
	xor	%edx,		%r11d		/* y ^ ... */
	lea	-1044525330(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r11d		/* x & ... */
	xor	%eax,		%r11d		/* z ^ ... */
	mov	4*4(%rsi),%r10d		/* (NEXT STEP) X[4] */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$22,		%ebx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%ecx,		%ebx		/* dst += x */
	xor	%ecx,		%r11d		/* y ^ ... */
	lea	-176418897(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r11d		/* x & ... */
	xor	%edx,		%r11d		/* z ^ ... */
	mov	5*4(%rsi),%r10d		/* (NEXT STEP) X[5] */
	add	%r11d,		%eax		/* dst += ... */
	rol	$7,		%eax		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%ebx,		%eax		/* dst += x */
	xor	%ebx,		%r11d		/* y ^ ... */
	lea	1200080426(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r11d		/* x & ... */
	xor	%ecx,		%r11d		/* z ^ ... */
	mov	6*4(%rsi),%r10d		/* (NEXT STEP) X[6] */
	add	%r11d,		%edx		/* dst += ... */
	rol	$12,		%edx		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%eax,		%edx		/* dst += x */
	xor	%eax,		%r11d		/* y ^ ... */
	lea	-1473231341(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r11d		/* x & ... */
	xor	%ebx,		%r11d		/* z ^ ... */
	mov	7*4(%rsi),%r10d		/* (NEXT STEP) X[7] */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$17,		%ecx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%edx,		%ecx		/* dst += x */
	xor	%edx,		%r11d		/* y ^ ... */
	lea	-45705983(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r11d		/* x & ... */
	xor	%eax,		%r11d		/* z ^ ... */
	mov	8*4(%rsi),%r10d		/* (NEXT STEP) X[8] */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$22,		%ebx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%ecx,		%ebx		/* dst += x */
	xor	%ecx,		%r11d		/* y ^ ... */
	lea	1770035416(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r11d		/* x & ... */
	xor	%edx,		%r11d		/* z ^ ... */
	mov	9*4(%rsi),%r10d		/* (NEXT STEP) X[9] */
	add	%r11d,		%eax		/* dst += ... */
	rol	$7,		%eax		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%ebx,		%eax		/* dst += x */
	xor	%ebx,		%r11d		/* y ^ ... */
	lea	-1958414417(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r11d		/* x & ... */
	xor	%ecx,		%r11d		/* z ^ ... */
	mov	10*4(%rsi),%r10d		/* (NEXT STEP) X[10] */
	add	%r11d,		%edx		/* dst += ... */
	rol	$12,		%edx		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%eax,		%edx		/* dst += x */
	xor	%eax,		%r11d		/* y ^ ... */
	lea	-42063(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r11d		/* x & ... */
	xor	%ebx,		%r11d		/* z ^ ... */
	mov	11*4(%rsi),%r10d		/* (NEXT STEP) X[11] */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$17,		%ecx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%edx,		%ecx		/* dst += x */
	xor	%edx,		%r11d		/* y ^ ... */
	lea	-1990404162(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r11d		/* x & ... */
	xor	%eax,		%r11d		/* z ^ ... */
	mov	12*4(%rsi),%r10d		/* (NEXT STEP) X[12] */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$22,		%ebx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%ecx,		%ebx		/* dst += x */
	xor	%ecx,		%r11d		/* y ^ ... */
	lea	1804603682(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r11d		/* x & ... */
	xor	%edx,		%r11d		/* z ^ ... */
	mov	13*4(%rsi),%r10d		/* (NEXT STEP) X[13] */
	add	%r11d,		%eax		/* dst += ... */
	rol	$7,		%eax		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%ebx,		%eax		/* dst += x */
	xor	%ebx,		%r11d		/* y ^ ... */
	lea	-40341101(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r11d		/* x & ... */
	xor	%ecx,		%r11d		/* z ^ ... */
	mov	14*4(%rsi),%r10d		/* (NEXT STEP) X[14] */
	add	%r11d,		%edx		/* dst += ... */
	rol	$12,		%edx		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%eax,		%edx		/* dst += x */
	xor	%eax,		%r11d		/* y ^ ... */
	lea	-1502002290(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r11d		/* x & ... */
	xor	%ebx,		%r11d		/* z ^ ... */
	mov	15*4(%rsi),%r10d		/* (NEXT STEP) X[15] */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$17,		%ecx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%edx,		%ecx		/* dst += x */
	xor	%edx,		%r11d		/* y ^ ... */
	lea	1236535329(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r11d		/* x & ... */
	xor	%eax,		%r11d		/* z ^ ... */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$22,		%ebx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%ecx,		%ebx		/* dst += x */
 mov	1*4(%rsi),	%r10d		/* (NEXT STEP) X[1] */
 mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
 mov	%edx,		%r12d		/* (NEXT STEP) z' = %edx */
	not	%r11d				/* not z */
	lea	-165796510(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r12d		/* x & z */
	and	%ecx,		%r11d		/* y & (not z) */
	mov	6*4(%rsi),%r10d		/* (NEXT STEP) X[6] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%r12d,		%eax		/* dst += ... */
	mov	%ecx,		%r12d		/* (NEXT STEP) z' = %ecx */
	rol	$5,		%eax		/* dst <<< s */
	add	%ebx,		%eax		/* dst += x */
	not	%r11d				/* not z */
	lea	-1069501632(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r12d		/* x & z */
	and	%ebx,		%r11d		/* y & (not z) */
	mov	11*4(%rsi),%r10d		/* (NEXT STEP) X[11] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%r12d,		%edx		/* dst += ... */
	mov	%ebx,		%r12d		/* (NEXT STEP) z' = %ebx */
	rol	$9,		%edx		/* dst <<< s */
	add	%eax,		%edx		/* dst += x */
	not	%r11d				/* not z */
	lea	643717713(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r12d		/* x & z */
	and	%eax,		%r11d		/* y & (not z) */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%r12d,		%ecx		/* dst += ... */
	mov	%eax,		%r12d		/* (NEXT STEP) z' = %eax */
	rol	$14,		%ecx		/* dst <<< s */
	add	%edx,		%ecx		/* dst += x */
	not	%r11d				/* not z */
	lea	-373897302(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r12d		/* x & z */
	and	%edx,		%r11d		/* y & (not z) */
	mov	5*4(%rsi),%r10d		/* (NEXT STEP) X[5] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%r12d,		%ebx		/* dst += ... */
	mov	%edx,		%r12d		/* (NEXT STEP) z' = %edx */
	rol	$20,		%ebx		/* dst <<< s */
	add	%ecx,		%ebx		/* dst += x */
	not	%r11d				/* not z */
	lea	-701558691(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r12d		/* x & z */
	and	%ecx,		%r11d		/* y & (not z) */
	mov	10*4(%rsi),%r10d		/* (NEXT STEP) X[10] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%r12d,		%eax		/* dst += ... */
	mov	%ecx,		%r12d		/* (NEXT STEP) z' = %ecx */
	rol	$5,		%eax		/* dst <<< s */
	add	%ebx,		%eax		/* dst += x */
	not	%r11d				/* not z */
	lea	38016083(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r12d		/* x & z */
	and	%ebx,		%r11d		/* y & (not z) */
	mov	15*4(%rsi),%r10d		/* (NEXT STEP) X[15] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%r12d,		%edx		/* dst += ... */
	mov	%ebx,		%r12d		/* (NEXT STEP) z' = %ebx */
	rol	$9,		%edx		/* dst <<< s */
	add	%eax,		%edx		/* dst += x */
	not	%r11d				/* not z */
	lea	-660478335(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r12d		/* x & z */
	and	%eax,		%r11d		/* y & (not z) */
	mov	4*4(%rsi),%r10d		/* (NEXT STEP) X[4] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%r12d,		%ecx		/* dst += ... */
	mov	%eax,		%r12d		/* (NEXT STEP) z' = %eax */
	rol	$14,		%ecx		/* dst <<< s */
	add	%edx,		%ecx		/* dst += x */
	not	%r11d				/* not z */
	lea	-405537848(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r12d		/* x & z */
	and	%edx,		%r11d		/* y & (not z) */
	mov	9*4(%rsi),%r10d		/* (NEXT STEP) X[9] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%r12d,		%ebx		/* dst += ... */
	mov	%edx,		%r12d		/* (NEXT STEP) z' = %edx */
	rol	$20,		%ebx		/* dst <<< s */
	add	%ecx,		%ebx		/* dst += x */
	not	%r11d				/* not z */
	lea	568446438(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r12d		/* x & z */
	and	%ecx,		%r11d		/* y & (not z) */
	mov	14*4(%rsi),%r10d		/* (NEXT STEP) X[14] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%r12d,		%eax		/* dst += ... */
	mov	%ecx,		%r12d		/* (NEXT STEP) z' = %ecx */
	rol	$5,		%eax		/* dst <<< s */
	add	%ebx,		%eax		/* dst += x */
	not	%r11d				/* not z */
	lea	-1019803690(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r12d		/* x & z */
	and	%ebx,		%r11d		/* y & (not z) */
	mov	3*4(%rsi),%r10d		/* (NEXT STEP) X[3] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%r12d,		%edx		/* dst += ... */
	mov	%ebx,		%r12d		/* (NEXT STEP) z' = %ebx */
	rol	$9,		%edx		/* dst <<< s */
	add	%eax,		%edx		/* dst += x */
	not	%r11d				/* not z */
	lea	-187363961(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r12d		/* x & z */
	and	%eax,		%r11d		/* y & (not z) */
	mov	8*4(%rsi),%r10d		/* (NEXT STEP) X[8] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%r12d,		%ecx		/* dst += ... */
	mov	%eax,		%r12d		/* (NEXT STEP) z' = %eax */
	rol	$14,		%ecx		/* dst <<< s */
	add	%edx,		%ecx		/* dst += x */
	not	%r11d				/* not z */
	lea	1163531501(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r12d		/* x & z */
	and	%edx,		%r11d		/* y & (not z) */
	mov	13*4(%rsi),%r10d		/* (NEXT STEP) X[13] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%r12d,		%ebx		/* dst += ... */
	mov	%edx,		%r12d		/* (NEXT STEP) z' = %edx */
	rol	$20,		%ebx		/* dst <<< s */
	add	%ecx,		%ebx		/* dst += x */
	not	%r11d				/* not z */
	lea	-1444681467(%eax,%r10d),%eax		/* Const + dst + ... */
	and	%ebx,		%r12d		/* x & z */
	and	%ecx,		%r11d		/* y & (not z) */
	mov	2*4(%rsi),%r10d		/* (NEXT STEP) X[2] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ecx,		%r11d		/* (NEXT STEP) z' = %ecx */
	add	%r12d,		%eax		/* dst += ... */
	mov	%ecx,		%r12d		/* (NEXT STEP) z' = %ecx */
	rol	$5,		%eax		/* dst <<< s */
	add	%ebx,		%eax		/* dst += x */
	not	%r11d				/* not z */
	lea	-51403784(%edx,%r10d),%edx		/* Const + dst + ... */
	and	%eax,		%r12d		/* x & z */
	and	%ebx,		%r11d		/* y & (not z) */
	mov	7*4(%rsi),%r10d		/* (NEXT STEP) X[7] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%ebx,		%r11d		/* (NEXT STEP) z' = %ebx */
	add	%r12d,		%edx		/* dst += ... */
	mov	%ebx,		%r12d		/* (NEXT STEP) z' = %ebx */
	rol	$9,		%edx		/* dst <<< s */
	add	%eax,		%edx		/* dst += x */
	not	%r11d				/* not z */
	lea	1735328473(%ecx,%r10d),%ecx		/* Const + dst + ... */
	and	%edx,		%r12d		/* x & z */
	and	%eax,		%r11d		/* y & (not z) */
	mov	12*4(%rsi),%r10d		/* (NEXT STEP) X[12] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%eax,		%r11d		/* (NEXT STEP) z' = %eax */
	add	%r12d,		%ecx		/* dst += ... */
	mov	%eax,		%r12d		/* (NEXT STEP) z' = %eax */
	rol	$14,		%ecx		/* dst <<< s */
	add	%edx,		%ecx		/* dst += x */
	not	%r11d				/* not z */
	lea	-1926607734(%ebx,%r10d),%ebx		/* Const + dst + ... */
	and	%ecx,		%r12d		/* x & z */
	and	%edx,		%r11d		/* y & (not z) */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	or	%r11d,		%r12d		/* (y & (not z)) | (x & z) */
	mov	%edx,		%r11d		/* (NEXT STEP) z' = %edx */
	add	%r12d,		%ebx		/* dst += ... */
	mov	%edx,		%r12d		/* (NEXT STEP) z' = %edx */
	rol	$20,		%ebx		/* dst <<< s */
	add	%ecx,		%ebx		/* dst += x */
 mov	5*4(%rsi),	%r10d		/* (NEXT STEP) X[5] */
 mov	%ecx,		%r11d		/* (NEXT STEP) y' = %ecx */
	lea	-378558(%eax,%r10d),%eax		/* Const + dst + ... */
	mov	8*4(%rsi),%r10d		/* (NEXT STEP) X[8] */
	xor	%edx,		%r11d		/* z ^ ... */
	xor	%ebx,		%r11d		/* x ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	rol	$4,		%eax		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) y' = %ebx */
	add	%ebx,		%eax		/* dst += x */
	lea	-2022574463(%edx,%r10d),%edx		/* Const + dst + ... */
	mov	11*4(%rsi),%r10d		/* (NEXT STEP) X[11] */
	xor	%ecx,		%r11d		/* z ^ ... */
	xor	%eax,		%r11d		/* x ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	rol	$11,		%edx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) y' = %eax */
	add	%eax,		%edx		/* dst += x */
	lea	1839030562(%ecx,%r10d),%ecx		/* Const + dst + ... */
	mov	14*4(%rsi),%r10d		/* (NEXT STEP) X[14] */
	xor	%ebx,		%r11d		/* z ^ ... */
	xor	%edx,		%r11d		/* x ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$16,		%ecx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) y' = %edx */
	add	%edx,		%ecx		/* dst += x */
	lea	-35309556(%ebx,%r10d),%ebx		/* Const + dst + ... */
	mov	1*4(%rsi),%r10d		/* (NEXT STEP) X[1] */
	xor	%eax,		%r11d		/* z ^ ... */
	xor	%ecx,		%r11d		/* x ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$23,		%ebx		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) y' = %ecx */
	add	%ecx,		%ebx		/* dst += x */
	lea	-1530992060(%eax,%r10d),%eax		/* Const + dst + ... */
	mov	4*4(%rsi),%r10d		/* (NEXT STEP) X[4] */
	xor	%edx,		%r11d		/* z ^ ... */
	xor	%ebx,		%r11d		/* x ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	rol	$4,		%eax		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) y' = %ebx */
	add	%ebx,		%eax		/* dst += x */
	lea	1272893353(%edx,%r10d),%edx		/* Const + dst + ... */
	mov	7*4(%rsi),%r10d		/* (NEXT STEP) X[7] */
	xor	%ecx,		%r11d		/* z ^ ... */
	xor	%eax,		%r11d		/* x ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	rol	$11,		%edx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) y' = %eax */
	add	%eax,		%edx		/* dst += x */
	lea	-155497632(%ecx,%r10d),%ecx		/* Const + dst + ... */
	mov	10*4(%rsi),%r10d		/* (NEXT STEP) X[10] */
	xor	%ebx,		%r11d		/* z ^ ... */
	xor	%edx,		%r11d		/* x ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$16,		%ecx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) y' = %edx */
	add	%edx,		%ecx		/* dst += x */
	lea	-1094730640(%ebx,%r10d),%ebx		/* Const + dst + ... */
	mov	13*4(%rsi),%r10d		/* (NEXT STEP) X[13] */
	xor	%eax,		%r11d		/* z ^ ... */
	xor	%ecx,		%r11d		/* x ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$23,		%ebx		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) y' = %ecx */
	add	%ecx,		%ebx		/* dst += x */
	lea	681279174(%eax,%r10d),%eax		/* Const + dst + ... */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	xor	%edx,		%r11d		/* z ^ ... */
	xor	%ebx,		%r11d		/* x ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	rol	$4,		%eax		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) y' = %ebx */
	add	%ebx,		%eax		/* dst += x */
	lea	-358537222(%edx,%r10d),%edx		/* Const + dst + ... */
	mov	3*4(%rsi),%r10d		/* (NEXT STEP) X[3] */
	xor	%ecx,		%r11d		/* z ^ ... */
	xor	%eax,		%r11d		/* x ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	rol	$11,		%edx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) y' = %eax */
	add	%eax,		%edx		/* dst += x */
	lea	-722521979(%ecx,%r10d),%ecx		/* Const + dst + ... */
	mov	6*4(%rsi),%r10d		/* (NEXT STEP) X[6] */
	xor	%ebx,		%r11d		/* z ^ ... */
	xor	%edx,		%r11d		/* x ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$16,		%ecx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) y' = %edx */
	add	%edx,		%ecx		/* dst += x */
	lea	76029189(%ebx,%r10d),%ebx		/* Const + dst + ... */
	mov	9*4(%rsi),%r10d		/* (NEXT STEP) X[9] */
	xor	%eax,		%r11d		/* z ^ ... */
	xor	%ecx,		%r11d		/* x ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$23,		%ebx		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) y' = %ecx */
	add	%ecx,		%ebx		/* dst += x */
	lea	-640364487(%eax,%r10d),%eax		/* Const + dst + ... */
	mov	12*4(%rsi),%r10d		/* (NEXT STEP) X[12] */
	xor	%edx,		%r11d		/* z ^ ... */
	xor	%ebx,		%r11d		/* x ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	rol	$4,		%eax		/* dst <<< s */
	mov	%ebx,		%r11d		/* (NEXT STEP) y' = %ebx */
	add	%ebx,		%eax		/* dst += x */
	lea	-421815835(%edx,%r10d),%edx		/* Const + dst + ... */
	mov	15*4(%rsi),%r10d		/* (NEXT STEP) X[15] */
	xor	%ecx,		%r11d		/* z ^ ... */
	xor	%eax,		%r11d		/* x ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	rol	$11,		%edx		/* dst <<< s */
	mov	%eax,		%r11d		/* (NEXT STEP) y' = %eax */
	add	%eax,		%edx		/* dst += x */
	lea	530742520(%ecx,%r10d),%ecx		/* Const + dst + ... */
	mov	2*4(%rsi),%r10d		/* (NEXT STEP) X[2] */
	xor	%ebx,		%r11d		/* z ^ ... */
	xor	%edx,		%r11d		/* x ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	rol	$16,		%ecx		/* dst <<< s */
	mov	%edx,		%r11d		/* (NEXT STEP) y' = %edx */
	add	%edx,		%ecx		/* dst += x */
	lea	-995338651(%ebx,%r10d),%ebx		/* Const + dst + ... */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	xor	%eax,		%r11d		/* z ^ ... */
	xor	%ecx,		%r11d		/* x ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	rol	$23,		%ebx		/* dst <<< s */
	mov	%ecx,		%r11d		/* (NEXT STEP) y' = %ecx */
	add	%ecx,		%ebx		/* dst += x */
 mov	0*4(%rsi),	%r10d		/* (NEXT STEP) X[0] */
 mov	$0xffffffff,	%r11d
 xor	%edx,		%r11d		/* (NEXT STEP) not z' = not %edx*/
	lea	-198630844(%eax,%r10d),%eax		/* Const + dst + ... */
	or	%ebx,		%r11d		/* x | ... */
	xor	%ecx,		%r11d		/* y ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	mov	7*4(%rsi),%r10d		/* (NEXT STEP) X[7] */
	mov	$0xffffffff,	%r11d
	rol	$6,		%eax		/* dst <<< s */
	xor	%ecx,		%r11d		/* (NEXT STEP) not z' = not %ecx */
	add	%ebx,		%eax		/* dst += x */
	lea	1126891415(%edx,%r10d),%edx		/* Const + dst + ... */
	or	%eax,		%r11d		/* x | ... */
	xor	%ebx,		%r11d		/* y ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	mov	14*4(%rsi),%r10d		/* (NEXT STEP) X[14] */
	mov	$0xffffffff,	%r11d
	rol	$10,		%edx		/* dst <<< s */
	xor	%ebx,		%r11d		/* (NEXT STEP) not z' = not %ebx */
	add	%eax,		%edx		/* dst += x */
	lea	-1416354905(%ecx,%r10d),%ecx		/* Const + dst + ... */
	or	%edx,		%r11d		/* x | ... */
	xor	%eax,		%r11d		/* y ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	mov	5*4(%rsi),%r10d		/* (NEXT STEP) X[5] */
	mov	$0xffffffff,	%r11d
	rol	$15,		%ecx		/* dst <<< s */
	xor	%eax,		%r11d		/* (NEXT STEP) not z' = not %eax */
	add	%edx,		%ecx		/* dst += x */
	lea	-57434055(%ebx,%r10d),%ebx		/* Const + dst + ... */
	or	%ecx,		%r11d		/* x | ... */
	xor	%edx,		%r11d		/* y ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	mov	12*4(%rsi),%r10d		/* (NEXT STEP) X[12] */
	mov	$0xffffffff,	%r11d
	rol	$21,		%ebx		/* dst <<< s */
	xor	%edx,		%r11d		/* (NEXT STEP) not z' = not %edx */
	add	%ecx,		%ebx		/* dst += x */
	lea	1700485571(%eax,%r10d),%eax		/* Const + dst + ... */
	or	%ebx,		%r11d		/* x | ... */
	xor	%ecx,		%r11d		/* y ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	mov	3*4(%rsi),%r10d		/* (NEXT STEP) X[3] */
	mov	$0xffffffff,	%r11d
	rol	$6,		%eax		/* dst <<< s */
	xor	%ecx,		%r11d		/* (NEXT STEP) not z' = not %ecx */
	add	%ebx,		%eax		/* dst += x */
	lea	-1894986606(%edx,%r10d),%edx		/* Const + dst + ... */
	or	%eax,		%r11d		/* x | ... */
	xor	%ebx,		%r11d		/* y ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	mov	10*4(%rsi),%r10d		/* (NEXT STEP) X[10] */
	mov	$0xffffffff,	%r11d
	rol	$10,		%edx		/* dst <<< s */
	xor	%ebx,		%r11d		/* (NEXT STEP) not z' = not %ebx */
	add	%eax,		%edx		/* dst += x */
	lea	-1051523(%ecx,%r10d),%ecx		/* Const + dst + ... */
	or	%edx,		%r11d		/* x | ... */
	xor	%eax,		%r11d		/* y ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	mov	1*4(%rsi),%r10d		/* (NEXT STEP) X[1] */
	mov	$0xffffffff,	%r11d
	rol	$15,		%ecx		/* dst <<< s */
	xor	%eax,		%r11d		/* (NEXT STEP) not z' = not %eax */
	add	%edx,		%ecx		/* dst += x */
	lea	-2054922799(%ebx,%r10d),%ebx		/* Const + dst + ... */
	or	%ecx,		%r11d		/* x | ... */
	xor	%edx,		%r11d		/* y ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	mov	8*4(%rsi),%r10d		/* (NEXT STEP) X[8] */
	mov	$0xffffffff,	%r11d
	rol	$21,		%ebx		/* dst <<< s */
	xor	%edx,		%r11d		/* (NEXT STEP) not z' = not %edx */
	add	%ecx,		%ebx		/* dst += x */
	lea	1873313359(%eax,%r10d),%eax		/* Const + dst + ... */
	or	%ebx,		%r11d		/* x | ... */
	xor	%ecx,		%r11d		/* y ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	mov	15*4(%rsi),%r10d		/* (NEXT STEP) X[15] */
	mov	$0xffffffff,	%r11d
	rol	$6,		%eax		/* dst <<< s */
	xor	%ecx,		%r11d		/* (NEXT STEP) not z' = not %ecx */
	add	%ebx,		%eax		/* dst += x */
	lea	-30611744(%edx,%r10d),%edx		/* Const + dst + ... */
	or	%eax,		%r11d		/* x | ... */
	xor	%ebx,		%r11d		/* y ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	mov	6*4(%rsi),%r10d		/* (NEXT STEP) X[6] */
	mov	$0xffffffff,	%r11d
	rol	$10,		%edx		/* dst <<< s */
	xor	%ebx,		%r11d		/* (NEXT STEP) not z' = not %ebx */
	add	%eax,		%edx		/* dst += x */
	lea	-1560198380(%ecx,%r10d),%ecx		/* Const + dst + ... */
	or	%edx,		%r11d		/* x | ... */
	xor	%eax,		%r11d		/* y ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	mov	13*4(%rsi),%r10d		/* (NEXT STEP) X[13] */
	mov	$0xffffffff,	%r11d
	rol	$15,		%ecx		/* dst <<< s */
	xor	%eax,		%r11d		/* (NEXT STEP) not z' = not %eax */
	add	%edx,		%ecx		/* dst += x */
	lea	1309151649(%ebx,%r10d),%ebx		/* Const + dst + ... */
	or	%ecx,		%r11d		/* x | ... */
	xor	%edx,		%r11d		/* y ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	mov	4*4(%rsi),%r10d		/* (NEXT STEP) X[4] */
	mov	$0xffffffff,	%r11d
	rol	$21,		%ebx		/* dst <<< s */
	xor	%edx,		%r11d		/* (NEXT STEP) not z' = not %edx */
	add	%ecx,		%ebx		/* dst += x */
	lea	-145523070(%eax,%r10d),%eax		/* Const + dst + ... */
	or	%ebx,		%r11d		/* x | ... */
	xor	%ecx,		%r11d		/* y ^ ... */
	add	%r11d,		%eax		/* dst += ... */
	mov	11*4(%rsi),%r10d		/* (NEXT STEP) X[11] */
	mov	$0xffffffff,	%r11d
	rol	$6,		%eax		/* dst <<< s */
	xor	%ecx,		%r11d		/* (NEXT STEP) not z' = not %ecx */
	add	%ebx,		%eax		/* dst += x */
	lea	-1120210379(%edx,%r10d),%edx		/* Const + dst + ... */
	or	%eax,		%r11d		/* x | ... */
	xor	%ebx,		%r11d		/* y ^ ... */
	add	%r11d,		%edx		/* dst += ... */
	mov	2*4(%rsi),%r10d		/* (NEXT STEP) X[2] */
	mov	$0xffffffff,	%r11d
	rol	$10,		%edx		/* dst <<< s */
	xor	%ebx,		%r11d		/* (NEXT STEP) not z' = not %ebx */
	add	%eax,		%edx		/* dst += x */
	lea	718787259(%ecx,%r10d),%ecx		/* Const + dst + ... */
	or	%edx,		%r11d		/* x | ... */
	xor	%eax,		%r11d		/* y ^ ... */
	add	%r11d,		%ecx		/* dst += ... */
	mov	9*4(%rsi),%r10d		/* (NEXT STEP) X[9] */
	mov	$0xffffffff,	%r11d
	rol	$15,		%ecx		/* dst <<< s */
	xor	%eax,		%r11d		/* (NEXT STEP) not z' = not %eax */
	add	%edx,		%ecx		/* dst += x */
	lea	-343485551(%ebx,%r10d),%ebx		/* Const + dst + ... */
	or	%ecx,		%r11d		/* x | ... */
	xor	%edx,		%r11d		/* y ^ ... */
	add	%r11d,		%ebx		/* dst += ... */
	mov	0*4(%rsi),%r10d		/* (NEXT STEP) X[0] */
	mov	$0xffffffff,	%r11d
	rol	$21,		%ebx		/* dst <<< s */
	xor	%edx,		%r11d		/* (NEXT STEP) not z' = not %edx */
	add	%ecx,		%ebx		/* dst += x */
	# add old values of A, B, C, D
	add	%r8d,	%eax
	add	%r9d,	%ebx
	add	%r14d,	%ecx
	add	%r15d,	%edx

	# loop control
	add	$64,		%rsi		# ptr += 64
	cmp	%rdi,		%rsi		# cmp end with ptr
	jb	2b				# jmp if ptr < end
	# END of loop over 16-word blocks
1:
	mov	%eax,		0*4(%rbp)	# ctx->A = A
	mov	%ebx,		1*4(%rbp)	# ctx->B = B
	mov	%ecx,		2*4(%rbp)	# ctx->C = C
	mov	%edx,		3*4(%rbp)	# ctx->D = D

	pop	%r15
	pop	%r14
	pop	%r13				# not really useful (r13 is unused)
	pop	%r12
	pop	%rbx
	pop	%rbp
	ret

#endif /* } USE_MD5_ASM */
