/* error codes returned by rsync */

#define RERR_SYNTAX     1       /* syntax or usage error */
#define RERR_PROTOCOL   2       /* protocol incompatibility */
#define RERR_FILESELECT 3       /* errors selecting input/output files, dirs */
#define RERR_UNSUPPORTED 4       /* requested action not supported */

#define RERR_SOCKETIO   10      /* error in socket IO */
#define RERR_FILEIO     11      /* error in file IO */
#define RERR_STREAMIO   12      /* error in rsync protocol data stream */
#define RERR_MESSAGEIO  13      /* errors with program diagnostics */
#define RERR_IPC        14      /* error in IPC code */

#define RERR_SIGNAL     20      /* status returned when sent SIGUSR1, SIGINT */
#define RERR_WAITCHILD  21      /* some error returned by waitpid() */
#define RERR_MALLOC     22      /* error allocating core memory buffers */

#define RERR_TIMEOUT    30      /* timeout in data send/receive */
