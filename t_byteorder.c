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
	static const uchar wire64[] = { 0xa5, 0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01, 0x5a };
	const int64 value64 = ((int64)0x01020304 << 32) | 0x05060708;
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
	memset(storage.bytes, 0, sizeof storage.bytes);
	storage.bytes[0] = (char)wire64[0];
	storage.bytes[9] = (char)wire64[9];
	SIVAL64(storage.bytes, 1, value64);
	if (memcmp(storage.bytes, wire64, sizeof wire64) || IVAL64(storage.bytes, 1) != value64) {
		fputs("Incorrect unaligned 64-bit wire encoding or decoding\n", stderr);
		return 1;
	}
#endif
	return 0;
}
