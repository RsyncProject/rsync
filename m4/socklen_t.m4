dnl Check for socklen_t.
dnl Native Windows uses Winsock rather than POSIX sys/socket.h.

AC_DEFUN([TYPE_SOCKLEN_T],
[
case "$host_os" in
  mingw*)
    dnl MinGW/Winsock uses int for socket address lengths.
    dnl Prefer the definition from ws2tcpip.h when available.
    AC_CHECK_TYPE([socklen_t],
      [],
      [AC_DEFINE([socklen_t], [int],
        [type to use in place of socklen_t if not defined])],
      [[
#include <winsock2.h>
#include <ws2tcpip.h>
      ]])
    ;;

  *)
    AC_CHECK_TYPE([socklen_t], ,[
      AC_MSG_CHECKING([for socklen_t equivalent])
      AC_CACHE_VAL([rsync_cv_socklen_t_equiv],
      [
        rsync_cv_socklen_t_equiv=
        for arg2 in "struct sockaddr" void; do
          for t in int size_t unsigned long "unsigned long"; do
            AC_COMPILE_IFELSE([AC_LANG_PROGRAM([[
#include <sys/types.h>
#include <sys/socket.h>
int getpeername (int, $arg2 *, $t *);
            ]],[[
$t len;
getpeername(0,0,&len);
            ]])],[
              rsync_cv_socklen_t_equiv="$t"
              break
            ])
          done
        done

        if test "x$rsync_cv_socklen_t_equiv" = x; then
          AC_MSG_ERROR([Cannot find a type to use in place of socklen_t])
        fi
      ])
      AC_MSG_RESULT($rsync_cv_socklen_t_equiv)
      AC_DEFINE_UNQUOTED(socklen_t, $rsync_cv_socklen_t_equiv,
        [type to use in place of socklen_t if not defined])],
      [#include <sys/types.h>
#include <sys/socket.h>])
    ;;
esac
])
