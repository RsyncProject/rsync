#include "rsync.h"

static OFF_T  last_ofs;
static struct timeval print_time;
static struct timeval start_time;
static OFF_T  start_ofs;

static unsigned long msdiff(struct timeval *t1, struct timeval *t2)
{
    return (t2->tv_sec - t1->tv_sec) * 1000
        + (t2->tv_usec - t1->tv_usec) / 1000;
}


/**
 * @param ofs Current position in file
 * @param size Total size of file
 * @param is_last True if this is the last time progress will be
 * printed for this file, so we should output a newline.  (Not
 * necessarily the same as all bytes being received.)
 **/
static void rprint_progress(OFF_T ofs, OFF_T size, struct timeval *now,
			    int is_last)
{
    int           pct  = (ofs == size) ? 100 : (int)((100.0*ofs)/size);
    unsigned long diff = msdiff(&start_time, now);
    double        rate = diff ? (double) (ofs-start_ofs) * 1000.0 / diff / 1024.0 : 0;
    const char    *units;
    /* If we've finished transferring this file, show the time taken;
     * otherwise show expected time to complete.  That's kind of
     * inconsistent, but people can probably cope.  Hopefully we'll
     * get more consistent and complete progress reporting soon. --
     * mbp */
    double        remain = is_last
                        ? (double) diff / 1000.0
                        : rate ? (double) (size-ofs) / rate / 1000.0 : 0.0;
    int 	  remain_h, remain_m, remain_s;

    if (rate > 1024*1024) {
	    rate /= 1024.0 * 1024.0;
	    units = "GB/s";
    } else if (rate > 1024) {
	    rate /= 1024.0;
	    units = "MB/s";
    } else {
	    units = "kB/s";
    }

    remain_s = (int) remain % 60;
    remain_m = (int) (remain / 60.0) % 60;
    remain_h = (int) (remain / 3600.0);
    
    rprintf(FINFO, "%12.0f %3d%% %7.2f%s %4d:%02d:%02d%s",
	    (double) ofs, pct, rate, units,
	    remain_h, remain_m, remain_s,
	    is_last ? "\n" : "\r");
}

void end_progress(OFF_T size)
{
	extern int do_progress, am_server;

	if (do_progress && !am_server) {
        	struct timeval now;
                gettimeofday(&now, NULL);
                rprint_progress(size, size, &now, True);
	}
	last_ofs   = 0;
        start_ofs  = 0;
        print_time.tv_sec  = print_time.tv_usec  = 0;
        start_time.tv_sec  = start_time.tv_usec  = 0;
}

void show_progress(OFF_T ofs, OFF_T size)
{
	extern int do_progress, am_server;
        struct timeval now;

        gettimeofday(&now, NULL);

        if (!start_time.tv_sec && !start_time.tv_usec) {
        	start_time.tv_sec  = now.tv_sec;
                start_time.tv_usec = now.tv_usec;
                start_ofs          = ofs;
        }

	if (do_progress
            && !am_server
            && ofs > last_ofs + 1000
            && msdiff(&print_time, &now) > 250) {
        	rprint_progress(ofs, size, &now, False);
                last_ofs = ofs;
                print_time.tv_sec  = now.tv_sec;
                print_time.tv_usec = now.tv_usec;
	}
}
