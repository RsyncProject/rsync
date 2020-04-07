AC_DEFUN([AC_HEADER_MAJOR_FIXED],
[AC_CACHE_CHECK(whether sys/types.h defines makedev,
		ac_cv_header_sys_types_h_makedev,
[AC_LINK_IFELSE([AC_LANG_PROGRAM([[@%:@include <sys/types.h>]],
				 [[return makedev(0, 0);]])],
		[if grep sys/sysmacros.h conftest.err >/dev/null; then
		   ac_cv_header_sys_types_h_makedev=no
		 else
		   ac_cv_header_sys_types_h_makedev=yes
		 fi],
		[ac_cv_header_sys_types_h_makedev=no])
])

if test $ac_cv_header_sys_types_h_makedev = no; then
AC_CHECK_HEADER(sys/mkdev.h,
		[AC_DEFINE(MAJOR_IN_MKDEV, 1,
			   [Define to 1 if `major', `minor', and `makedev' are
			    declared in <mkdev.h>.])])

  if test $ac_cv_header_sys_mkdev_h = no; then
    AC_CHECK_HEADER(sys/sysmacros.h,
		    [AC_DEFINE(MAJOR_IN_SYSMACROS, 1,
			       [Define to 1 if `major', `minor', and `makedev'
				are declared in <sysmacros.h>.])])
  fi
fi
])
