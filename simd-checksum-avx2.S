.intel_syntax noprefix
.text
	.p2align 5
	.globl get_checksum1_avx2

get_checksum1_avx2:
	vpmovzxbw	ymm7, XMMWORD PTR .mul_T1[rip]
	vmovd	xmm6,[rcx]
	lea	eax, [rsi-128]
	vpcmpeqd	ymm4, ymm4, ymm4
	cmp	edx, eax
	jge	.exit
	mov	eax, 0x01020304
	vmovd	xmm5, eax
	vpbroadcastd	ymm5, xmm5
	vpabsb	ymm4, ymm4
	lea	rax, [rdi+rdx]
	sub	esi, 65
	vmovdqu ymm8,  [rax]
	vmovdqu ymm13, [rax+32]
	sub	esi, edx
	lea	r9, [rax+128]
	and	esi, ~63
	add	r9, rsi
	add	rax, 64
	vpxor	xmm1, xmm1, xmm1
	vpxor	xmm15,xmm15,xmm15
	.p2align 5

#ifdef IACA
	mov 	ebx, 111
	.byte 0x64, 0x67, 0x90
#endif
.loop:
	vpmaddubsw	ymm0, ymm4, ymm8
	vpmaddubsw	ymm9, ymm4, ymm13
	vmovdqu	ymm14, [rax]
	vmovdqu	ymm2,  [rax+32]
	vpaddd	ymm15, ymm15, ymm6
	add	rax, 64
	vpaddw	ymm11, ymm0, ymm9
	vpsrld	ymm12, ymm11, 16
	vpaddw	ymm11, ymm11, ymm12
	vpaddd	ymm6, ymm6, ymm11
	vphaddsw	ymm10, ymm0, ymm9
	vpmaddubsw	ymm8, ymm5, ymm8
	vpmaddubsw	ymm3, ymm5, ymm13
	prefetcht0	[rax+384]
	vpaddw	ymm3, ymm3, ymm8
	vpsrld	ymm8, ymm3, 16
	vpmaddwd	ymm0, ymm10, ymm7
	vpaddd	ymm8, ymm8, ymm0
	vpaddd	ymm3, ymm3, ymm8
	vpaddd	ymm1, ymm1, ymm3
	vmovdqa ymm8, ymm14
	vmovdqa ymm13, ymm2
	cmp	r9, rax
	jne	.loop

#ifdef IACA
	mov 	ebx, 222
	.byte 0x64, 0x67, 0x90
#endif

	vpslld	ymm15, ymm15, 6
	vpsrldq	ymm11, ymm6, 4
	lea	rax, [rdx+64]
	add	rax, rsi
	
	vpaddd  ymm0, ymm1, ymm15
	vpaddd	ymm6, ymm11, ymm6
	vpsrldq	ymm10, ymm0, 4
	
	vpsrldq	ymm11, ymm6, 8
	vpaddd	ymm0, ymm0, ymm10
	vpsrldq	ymm10, ymm0, 8
	vpaddd	ymm6, ymm6, ymm11
	vpaddd	ymm0, ymm0, ymm10
	vextracti128	xmm2, ymm6, 0x1
	vextracti128	xmm1, ymm0, 0x1
	vpaddd	xmm6, xmm2, xmm6
	vpaddd	xmm1, xmm1, xmm0
	vmovd	[rcx], xmm6
	vmovd	edx, xmm1
	add	edx, [r8]
	mov	[r8], edx
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
