#include "rsync.h"

 int main(int argc, char *argv[])
{
	STRUCT_STAT st;

	while (--argc > 0) {
		if (stat(*++argv, &st) < 0) {
			fprintf(stderr, "Unable to stat `%s'\n", *argv);
			exit(1);
		}
		printf("%ld/%ld\n", (long)major(st.st_dev),
				    (long)minor(st.st_dev));
	}

	return 0;
}
