
#ifdef __APPLE__
#define get_checksum1_avx2  _get_checksum1_avx2
#endif

.intel_syntax noprefix
.text
	.p2align 5
	.globl get_checksum1_avx2

# rdi=*buf, rsi=len, edx=i, rcx= *ps1, r8= *ps2
get_checksum1_avx2:
	vmovd	xmm6,[rcx] # load *ps1
	lea	eax, [rsi-128]
	cmp	edx, eax
	jge	.exit
	lea	rax, .mul_T2[rip]
	vmovntdqa ymm7, [rax]   # load T2 multiplication constants
	vmovntdqa ymm12,[rax+32]# from memory.
	vpcmpeqd	ymm15, ymm15, ymm15 #set all elements to -1.

#if CHAR_OFFSET != 0
	mov	eax, 32*CHAR_OFFSET
	vmovd	xmm10, eax
	vpbroadcastd	ymm10, xmm10
	mov	eax, 528*CHAR_OFFSET
	vmovd	xmm13, eax
	vpbroadcastd ymm13, xmm13
#endif
	vpabsb	ymm15, ymm15  # set all byte size elements to 1.
	lea	rax, [rdi+rdx]
	sub	esi, 65
	vmovdqu ymm2, [rax]   # preload the first 64 bytes.
	vmovdqu ymm3, [rax+32]
	sub	esi, edx
	lea	rdi, [rax+128]
	add	rax, 64
	and	rsi, ~63
	add	rdi, rsi
	prefetcht0 [rax+256]
	vpxor	xmm1, xmm1, xmm1 # reset both partial sums accumulators.
	vpxor	xmm4, xmm4, xmm4
	.p2align 5 	# 32 byte align to maximize uops cache utilization.
			# should fit into the LSD allocation queue.
.loop:
	vpmaddubsw	ymm0, ymm15, ymm2  # s1 partial sums
	vpmaddubsw	ymm5, ymm15, ymm3 
	vmovdqu	ymm8,  [rax]	# preload the next 
	vmovdqu	ymm9,  [rax+32] # 64 bytes.
	add	rax, 64
	vpaddd	ymm4, ymm4, ymm6
	vpaddw	ymm5, ymm5, ymm0
	vpsrld	ymm0, ymm5, 16
	vpaddw	ymm5, ymm0, ymm5
	vpaddd	ymm6, ymm5, ymm6
	vpmaddubsw	ymm2, ymm7, ymm2  # s2 partial sums
	vpmaddubsw	ymm3, ymm12, ymm3
	prefetcht0	[rax+384] # prefetch 6 cachelines ahead.
	vpaddw	ymm3, ymm2, ymm3
	vpsrldq	ymm2, ymm3, 2
	vpaddd	ymm3, ymm2, ymm3
	vpaddd	ymm1, ymm1, ymm3

#if CHAR_OFFSET != 0
	vpaddd ymm6, ymm10, ymm6  #  32*CHAR_OFFSET
	vpaddd ymm1, ymm13, ymm1  # 528*CHAR_OFFSET
#endif
	vmovdqa ymm2, ymm8   # move the next 64 bytes 
	vmovdqa ymm3, ymm9   # into the right registers
	cmp	rdi, rax
	jne	.loop

	# now we reduce the partial sums.
	vpslld	ymm3, ymm4, 6
	vpsrldq	ymm2, ymm6, 4
	lea	rax, [rdx+64]
	mov	edx, [r8] # load from *ps2
	
	vpaddd  ymm0, ymm3, ymm1
	vpaddd	ymm6, ymm2, ymm6
	vpsrlq	ymm3, ymm0, 32
	add	rax, rsi
	
	vpsrldq	ymm2, ymm6, 8
	vpaddd	ymm0, ymm3, ymm0
	vpsrldq	ymm3, ymm0, 8
	vpaddd	ymm6, ymm2, ymm6
	vpaddd	ymm0, ymm3, ymm0
	vextracti128	xmm2, ymm6, 0x1
	vextracti128	xmm1, ymm0, 0x1
	vpaddd	xmm6, xmm2, xmm6
	vmovd	[rcx], xmm6
	vpaddd	xmm1, xmm1, xmm0
	vmovd	ecx, xmm1
	add	edx, ecx
	mov	[r8], edx
	vzeroupper
	ret
.exit:
	mov	eax, edx
	ret

#ifdef __APPLE__
.data
	.balign 64
#else
.section	.rodata
	.p2align 6 
#endif
.mul_T2: 
	.byte 64
	.byte 63
	.byte 62
	.byte 61
	.byte 60
	.byte 59
	.byte 58
	.byte 57
	.byte 56
	.byte 55
	.byte 54
	.byte 53
	.byte 52
	.byte 51
	.byte 50
	.byte 49
	.byte 48
	.byte 47
	.byte 46
	.byte 45
	.byte 44
	.byte 43
	.byte 42
	.byte 41
	.byte 40
	.byte 39
	.byte 38
	.byte 37
	.byte 36
	.byte 35
	.byte 34
	.byte 33
	.byte 32
	.byte 31
	.byte 30
	.byte 29
	.byte 28
	.byte 27
	.byte 26
	.byte 25
	.byte 24
	.byte 23
	.byte 22
	.byte 21
	.byte 20
	.byte 19
	.byte 18
	.byte 17
	.byte 16
	.byte 15
	.byte 14
	.byte 13
	.byte 12
	.byte 11
	.byte 10
	.byte 9
	.byte 8
	.byte 7
	.byte 6
	.byte 5
	.byte 4
	.byte 3
	.byte 2
	.byte 1
