.intel_syntax noprefix
.text
	.p2align 5
	.globl get_checksum1_avx2

// rdi=*buf, rsi=len, edx=i, rcx= *ps1, r8= *ps2
get_checksum1_avx2:
	vmovntdqa xmm7, XMMWORD PTR .mul_T1[rip]
	vpmovzxbw	ymm7, xmm7 //XMMWORD PTR .mul_T1[rip] // load T1 multiplication constants
	vmovd	xmm6,[rcx] // *ps1
	lea	eax, [rsi-128]
	vpcmpeqd	ymm15, ymm15, ymm15
	cmp	edx, eax
	jge	.exit
	mov	eax, 0x01020304
	vmovd	xmm12, eax
	vpbroadcastd	ymm12, xmm12

#if CHAR_OFFSET != 0
	mov	eax, 32*CHAR_OFFSET
	vmovd	xmm10, eax
	vpbroadcastd	ymm10, xmm10
	mov	eax, 528*CHAR_OFFSET
	vmovd	xmm13, eax
	vpbroadcastd ymm13, xmm13
#endif
	vpabsb	ymm15, ymm15
	lea	rax, [rdi+rdx]
	sub	esi, 65
	vmovdqu ymm2, [rax]
	vmovdqu ymm3, [rax+32]
	sub	esi, edx
	lea	r9, [rax+128]
	and	esi, ~63
	add	r9, rsi
	add	rax, 64
	vpxor	xmm1, xmm1, xmm1
	vpxor	xmm4,xmm4,xmm4
	.p2align 5

#ifdef IACA
	mov 	ebx, 111
	.byte 0x64, 0x67, 0x90
#endif
.loop:
	vpmaddubsw	ymm0, ymm15, ymm2
	vpmaddubsw	ymm9, ymm15, ymm3
	vmovdqu	ymm14, [rax]
	vmovdqu	ymm8,  [rax+32]
	vpaddd	ymm4, ymm4, ymm6
	add	rax, 64
	vpaddw	ymm5, ymm9, ymm0
	vpsrld	ymm11, ymm5, 16
	vpaddw	ymm5, ymm11, ymm5
	vpaddd	ymm6, ymm5, ymm6
	vphaddsw	ymm0, ymm0, ymm9
	vpmaddubsw	ymm2, ymm12, ymm2
	vpmaddubsw	ymm3, ymm12, ymm3
	prefetcht0	[rax+384]
	vpaddw	ymm3, ymm2, ymm3
	vpsrld	ymm2, ymm3, 16
	vpmaddwd	ymm0, ymm0, ymm7
	vpaddd	ymm2, ymm0, ymm2
	vpaddd	ymm3, ymm2, ymm3
	vpaddd	ymm1, ymm1, ymm3
#if CHAR_OFFSET != 0
	vpaddd ymm6, ymm10, ymm6  // 32*CHAR_OFFSET
	vpaddd ymm1, ymm13, ymm1  //528*CHAR_OFFSET
#endif
	vmovdqa ymm2, ymm14
	vmovdqa ymm3, ymm8
	cmp	r9, rax
	jne	.loop

#ifdef IACA
	mov 	ebx, 222
	.byte 0x64, 0x67, 0x90
#endif

	vpslld	ymm3, ymm4, 6
	vpsrldq	ymm2, ymm6, 4
	lea	rax, [rdx+64]
	vmovd	xmm4, [r8] // ps2
	
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
	vpaddd	xmm4, xmm4, xmm0
	vpaddd	xmm1, xmm1, xmm4
	vmovd	[r8], xmm1
	vzeroupper
	ret
.exit:
	mov	eax, edx
	ret


.section	.rodata
	.align 128

.mul_T1:
	.byte	60
	.byte	56
	.byte	52
	.byte	48
	.byte	28
	.byte	24
	.byte	20
	.byte	16
	.byte	44
	.byte	40
	.byte	36
	.byte	32
	.byte	12
	.byte	8
	.byte	4
	.byte	0
