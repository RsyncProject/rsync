#include "rsync.h"

int main(UNUSED(int argc), UNUSED(char *argv[]))
{
	union {
		int64 alignment;
		char bytes[10];
	} storage;
	static const uchar wire32[] = { 0xa5, 0xef, 0xcd, 0xab, 0x89, 0x5a };
	const uint32 value32 = 0x89abcdef;
#if SIZEOF_INT64 >= 8
	static const struct {
		int64 value;
		uchar wire[8];
	} cases[] = {
		{ ((int64)0x01020304 << 32) | 0x05060708, { 8, 7, 6, 5, 4, 3, 2, 1 } },
		{ -1, { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff } },
		{ -((int64)1 << 62) - ((int64)1 << 62), { 0, 0, 0, 0, 0, 0, 0, 0x80 } },
	};
	size_t i;
#endif

	memset(storage.bytes, 0, sizeof storage.bytes);
	storage.bytes[0] = (char)wire32[0];
	storage.bytes[5] = (char)wire32[5];
	SIVAL(storage.bytes, 1, value32);
	if (memcmp(storage.bytes, wire32, sizeof wire32) || IVAL(storage.bytes, 1) != value32) {
		fputs("Incorrect unaligned 32-bit wire encoding or decoding\n", stderr);
		return 1;
	}
#if SIZEOF_INT64 >= 8
	for (i = 0; i < sizeof cases / sizeof cases[0]; i++) {
		memset(storage.bytes, 0, sizeof storage.bytes);
		storage.bytes[0] = 'a';
		storage.bytes[9] = 'z';
		SIVAL64(storage.bytes, 1, cases[i].value);
		if (storage.bytes[0] != 'a' || storage.bytes[9] != 'z'
		 || memcmp(storage.bytes + 1, cases[i].wire, sizeof cases[i].wire)
		 || IVAL64(storage.bytes, 1) != cases[i].value) {
			fputs("Incorrect unaligned 64-bit wire encoding or decoding\n", stderr);
			return 1;
		}
	}
#endif
	return 0;
}
