#include "rsync.h"

 int main(int argc, char *argv[])
{
	STRUCT_STAT st;
	int ret;

	while (--argc > 0) {
#ifdef USE_STAT64_FUNCS
		ret = stat64(*++argv, &st);
#else
		ret = stat(*++argv, &st);
#endif
		if (ret < 0) {
			fprintf(stderr, "Unable to stat `%s'\n", *argv);
			exit(1);
		}
		printf("%ld/%ld\n", (long)major(st.st_dev),
				    (long)minor(st.st_dev));
	}

	return 0;
}
