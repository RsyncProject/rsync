#
# CMake build for rsync.
#
# This is an alternative to the autoconf build (configure && make).  It exists
# mainly so that rsync can be built on Windows with MSVC/clang-cl, where there
# is no shell, no awk and no autoconf -- but it is a full replacement on
# Linux/macOS/BSD too.
#
#   cmake -B build -G Ninja
#   cmake --build build
#
# Copyright (C) 2026 Max Vilimpoc, rsync CMake/Windows port.
# Distributed under the same GPL-3.0-or-later terms as the rest of rsync.
#
cmake_minimum_required(VERSION 3.20)

# version.h is where rsync itself keeps the version -- configure.ac greps it
# out of there too -- so read it rather than repeating it.  A literal here
# goes stale silently: it said 3.4.4 while version.h said 3.5.0dev.
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/version.h" _version_h)
string(REGEX MATCH "RSYNC_VERSION[ \t]+\"([^\"]+)\"" _m "${_version_h}")
set(RSYNC_VERSION_STRING "${CMAKE_MATCH_1}")
if(NOT RSYNC_VERSION_STRING)
    message(FATAL_ERROR "Could not read RSYNC_VERSION from version.h")
endif()
# project(VERSION) takes numbers only, so drop any "dev"/"pre1" suffix.
string(REGEX MATCH "^[0-9]+(\\.[0-9]+)*" RSYNC_VERSION_NUMERIC "${RSYNC_VERSION_STRING}")

project(rsync
        VERSION ${RSYNC_VERSION_NUMERIC}
        DESCRIPTION "fast incremental file transfer"
        HOMEPAGE_URL "https://rsync.samba.org/"
        LANGUAGES C)

include(CheckCSourceCompiles)
include(CheckCSourceRuns)
include(CheckFunctionExists)
include(CheckIncludeFile)
include(CheckStructHasMember)
include(CheckSymbolExists)
include(CheckTypeSize)
include(CMakePushCheckState)
include(TestBigEndian)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
option(RSYNC_ENABLE_ACLS      "Support POSIX ACLs"              ON)
option(RSYNC_ENABLE_XATTRS    "Support extended attributes"     ON)
option(RSYNC_ENABLE_OPENSSL   "Use OpenSSL for MD4/MD5"         OFF)
option(RSYNC_ENABLE_ZSTD      "Support zstd compression"        OFF)
option(RSYNC_ENABLE_LZ4       "Support LZ4 compression"         OFF)
option(RSYNC_ENABLE_XXHASH    "Support xxhash checksums"        OFF)
option(RSYNC_ENABLE_ICONV     "Support --iconv character conversion" OFF)
option(RSYNC_ENABLE_SIMD      "Use SIMD/asm checksum acceleration" OFF)
option(RSYNC_EXTERNAL_ZLIB    "Link the system zlib instead of the bundled copy" OFF)
option(RSYNC_STATIC_CRT       "Link the C runtime statically (MSVC only)" ON)
option(RSYNC_HARDEN           "Enable exploit mitigations (MSVC only)" ON)
option(RSYNC_STRICT_MITIGATIONS
       "Also enable ACG, CIG and strict handle checks -- see BUILD-CMAKE.md" OFF)
option(RSYNC_ENABLE_ASAN      "Build with AddressSanitizer (for testing)" OFF)
# Note: the test suite (runtests.py) and its C helpers are driven by the
# autoconf Makefile's "make check"; CMake builds only rsync itself.

set(RSYNC_PATH      "rsync" CACHE STRING "Default remote rsync command")
set(RSYNC_RSH       "ssh"   CACHE STRING "Default remote shell")
set(NOBODY_USER     "nobody" CACHE STRING "Unprivileged user for the daemon")
set(NOBODY_GROUP    "nogroup" CACHE STRING "Unprivileged group for the daemon")

set(RSYNC_PLATFORM_INCLUDE "")
set(RSYNC_PLATFORM_INCLUDE_DIR "")

if(WIN32)
    # Windows XP is the compatibility floor for the native 32-bit build.
    # Keeping these definitions global ensures both feature probes and the
    # final target see the XP API surface instead of accidentally importing a
    # Vista-or-newer entry point from the host SDK.
    add_compile_definitions(
        WINVER=0x0501
        _WIN32_WINNT=0x0501
        _WIN32_IE=0x0600
        RSYNC_XP_TARGET=1)

    # The 64-bit build is the one people want, so it keeps the plain name; a
    # 32-bit build is named for what it is, so both can sit in one directory
    # and neither can be mistaken for the other.  The toolchain decides which
    # this is -- with Ninja that is whichever vcvars was run -- and everything
    # downstream (the version resource, the test suite, what CI ships) follows
    # from these two variables rather than guessing again.
    if(CMAKE_SIZEOF_VOID_P EQUAL 4)
        set(RSYNC_WIN_ARCH "x86")
        set(RSYNC_EXE_NAME "rsync-x86")
    else()
        set(RSYNC_WIN_ARCH "x64")
        set(RSYNC_EXE_NAME "rsync")
    endif()

    set(RSYNCD_SYSCONF  "rsyncd.conf")
    set(POPT_SYSCONFDIR ".")
    set(RSYNC_PLATFORM_INCLUDE "#include \"win32/win32compat.h\"")
    # Shim headers (pwd.h, dirent.h, syslog.h, ...) so the shared sources
    # can include them unconditionally.
    set(RSYNC_PLATFORM_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/win32/include")
    # Always defined, so win32harden.c can branch on it with a plain `if`
    # rather than a preprocessor conditional.
    add_compile_definitions(RSYNC_STRICT_MITIGATIONS=$<BOOL:${RSYNC_STRICT_MITIGATIONS}>)
else()
    set(RSYNCD_SYSCONF  "/etc/rsyncd.conf")
    set(POPT_SYSCONFDIR "/etc")
endif()

# Arch and several current MinGW distributions default to UCRT, whose
# api-ms-win-crt DLLs do not exist on XP.  Select the system msvcrt runtime,
# link GCC's support libraries into the executable, and stamp the PE image
# with the actual compatibility floor.
if(MINGW)
    add_compile_options(-mcrtdll=msvcrt-os)
    add_link_options(
        -mcrtdll=msvcrt-os
        -static
        -static-libgcc
        "LINKER:-u,___ms_vsnprintf"
        "LINKER:--subsystem,console:5.01"
        "LINKER:--major-os-version,5"
        "LINKER:--minor-os-version,1"
        "LINKER:--major-subsystem-version,5"
        "LINKER:--minor-subsystem-version,1")
endif()

# A default /MD build imports the C runtime from VCRUNTIME140.dll and the
# api-ms-win-crt-*.dll UCRT stubs, so the binary only runs where the VC++
# redistributable is installed and the UCRT is up to date.  rsync.exe is the
# sort of thing that gets copied onto a machine and run, so link the runtime
# in: what is left is ws2_32, advapi32 and kernel32, which are part of Windows
# itself.  Costs about 245 KB (485 KB -> 737 KB for a Release build).
#
# This must be set before any target is created; CMake seeds each target's
# MSVC_RUNTIME_LIBRARY property from it (policy CMP0091, NEW since 3.15).
if(MSVC AND RSYNC_STATIC_CRT)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
endif()

# ---------------------------------------------------------------------------
# Exploit mitigations
#
# rsync parses input it does not control -- a file list, a checksum stream and
# a set of deltas, all produced by the far end -- in C, so the image-level
# mitigations are worth having.  These are the ones the toolchain can bake in;
# the ones that have to be switched on at runtime live in win32/win32harden.c.
#
# Check the result with:  dumpbin /headers /loadconfig build\rsync.exe
# ---------------------------------------------------------------------------
if(MSVC AND RSYNC_HARDEN)
    add_compile_options(
        /GS             # stack cookies -- MSVC's default, pinned deliberately
        /sdl            # strict /GS, so the cookie goes on functions the
                        # default heuristic skips.  It also promotes a set of
                        # warnings to errors, which is the cost: an upstream
                        # merge can break the Windows build and has to be
                        # looked at rather than ignored
        /guard:cf       # CFG: indirect calls checked against a table of
                        # legitimate targets, so a clobbered function pointer
                        # cannot be aimed at an arbitrary address
        /Qspectre)      # speculative-execution hardening for the bounds-check
                        # patterns that Spectre v1 preys on
    add_link_options(
        /guard:cf                  # the other half of CFG: emit the table
        /CETCOMPAT                 # opt in to the CPU shadow stack where there
                                   # is one, which defeats ROP at the return
        /DYNAMICBASE /NXCOMPAT)    # ASLR and DEP, pinned as above
    if(CMAKE_SIZEOF_VOID_P EQUAL 4)
        # 64-bit ASLR has nothing to randomise in a 32-bit address space, and
        # asking for it is a hard error (LNK1246) rather than a warning.  What
        # a 32-bit build can use instead is the top half of its own address
        # space: without this the loader caps it at 2 GB, and rsync's file
        # list is the one structure that can get big enough to care.
        add_link_options(/LARGEADDRESSAWARE)
    else()
        add_link_options(/HIGHENTROPYVA)   # 64-bit ASLR: full-entropy rebasing
    endif()
    if(NOT RSYNC_ENABLE_ASAN)
        # LOAD_LIBRARY_SEARCH_SYSTEM32: resolve the static imports from
        # System32 alone, so a DLL planted beside the executable cannot win.
        # Left out under ASan, whose runtime DLL lives in the MSVC toolchain
        # directory: the loader would refuse to look there and the binary
        # would die at startup with STATUS_DLL_NOT_FOUND.
        add_link_options(/DEPENDENTLOADFLAG:0x800)
    endif()
endif()

if(RSYNC_ENABLE_ASAN)
    # A testing tool, not a shipping configuration: it makes rsync several
    # times slower and needs the ASan runtime present.  Worth running the
    # Windows suite under once in a while -- see BUILD-CMAKE.md.
    if(MSVC)
        add_compile_options(/fsanitize=address /Zi)
        add_link_options(/INCREMENTAL:NO)
    else()
        add_compile_options(-fsanitize=address -fno-omit-frame-pointer -g)
        add_link_options(-fsanitize=address)
    endif()
endif()

set(GEN_DIR "${CMAKE_CURRENT_BINARY_DIR}")

# rsync.h does #include "config.h", and a quoted include is resolved against
# the including file's own directory before any -I path.  So a config.h left
# in the source tree by a previous ./configure silently wins over ours, and
# the mismatch shows up much later as bizarre link errors.
foreach(stale config.h proto.h daemon-parm.h rounding.h)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/${stale}")
        message(FATAL_ERROR
            "Found ${stale} in the source tree, left over from an autoconf "
            "build.  It would shadow the copy CMake generates.  Run "
            "'make distclean' (or delete the generated *.h files) first.")
    endif()
endforeach()

# ---------------------------------------------------------------------------
# Generated headers (these replace the awk rules in Makefile.in)
#
# They are produced at configure time because the rounding.h probe below has
# to be able to #include rsync.h, which in turn needs proto.h.
# ---------------------------------------------------------------------------
find_package(Python3 3.6 REQUIRED COMPONENTS Interpreter)

set(GEN_SCRIPT "${CMAKE_CURRENT_SOURCE_DIR}/cmake/gen-headers.py")

function(rsync_generate name)
    execute_process(
        COMMAND ${Python3_EXECUTABLE} ${GEN_SCRIPT} ${ARGN}
        RESULT_VARIABLE _rc
        OUTPUT_VARIABLE _out
        ERROR_VARIABLE _err)
    if(NOT _rc EQUAL 0)
        message(FATAL_ERROR "Failed to generate ${name}:\n${_out}${_err}")
    endif()
endfunction()

rsync_generate("daemon-parm.h" daemon-parm
    -o "${GEN_DIR}/daemon-parm.h"
    "${CMAKE_CURRENT_SOURCE_DIR}/daemon-parm.txt")

rsync_generate("help-rsync.h" help
    -o "${GEN_DIR}/help-rsync.h" "${CMAKE_CURRENT_SOURCE_DIR}/rsync.1.md")
rsync_generate("help-rsyncd.h" help
    -o "${GEN_DIR}/help-rsyncd.h" "${CMAKE_CURRENT_SOURCE_DIR}/rsync.1.md")
rsync_generate("default-cvsignore.h" define
    -o "${GEN_DIR}/default-cvsignore.h" "${CMAKE_CURRENT_SOURCE_DIR}/rsync.1.md")
rsync_generate("default-dont-compress.h" define
    -o "${GEN_DIR}/default-dont-compress.h" "${CMAKE_CURRENT_SOURCE_DIR}/rsync.1.md")

file(GLOB PROTO_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/*.c")
list(SORT PROTO_SOURCES)
rsync_generate("proto.h" proto
    -o "${GEN_DIR}/proto.h"
    ${PROTO_SOURCES}
    "${CMAKE_CURRENT_SOURCE_DIR}/lib/compat.c"
    "${GEN_DIR}/daemon-parm.h")

# Re-run cmake when any generator input changes.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/daemon-parm.txt"
    "${CMAKE_CURRENT_SOURCE_DIR}/rsync.1.md"
    "${GEN_SCRIPT}")

# ---------------------------------------------------------------------------
# Version
#
# This port is the Windows adaptation layer for a *released* rsync: the branch
# sits on an upstream release tag, and releases here are cut only from those
# points.  So the upstream half of the version is simply what version.h says,
# and the only thing git has to supply is which commit here built it:
#
#     3.5.0-g9273e229
#     |     `-- this repository's HEAD
#     `-------- the upstream rsync release, from version.h
#
# Nothing here consults a remote ref.  An earlier scheme named the upstream
# commit too, found via `merge-base HEAD master`, which quietly reported a
# stale answer whenever the local master had not been fetched -- and after a
# rebase onto a release that is exactly when it is stale.  version.h cannot
# drift from the source it ships with, so it needs no fetching to be right.
#
# It also self-flags misuse: upstream sets version.h to "3.5.1dev" for the
# whole development cycle, so a branch rebased onto master mid-cycle builds as
# 3.5.1dev-g<rev>, which is visibly not a release, and the release workflow
# refuses to publish it.
# ---------------------------------------------------------------------------
set(RSYNC_PORT_REV "")
set(RSYNC_PORT_COUNT 0)

find_package(Git QUIET)
if(GIT_FOUND AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/.git")
    execute_process(COMMAND ${GIT_EXECUTABLE} rev-parse --short=8 HEAD
                    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                    OUTPUT_VARIABLE RSYNC_PORT_REV
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)

    # The binary FILEVERSION needs a number that advances with every commit
    # here (see the version resource below).  Commits since version.h last
    # changed is that number, and asking git which commit last touched one
    # file needs no tags and no remote refs.  It resets when upstream bumps
    # the version, but the release fields ahead of it rise at the same moment,
    # so two builds still order correctly.
    execute_process(COMMAND ${GIT_EXECUTABLE} log -1 --format=%H -- version.h
                    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                    OUTPUT_VARIABLE _vbump
                    OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(_vbump)
        execute_process(COMMAND ${GIT_EXECUTABLE} rev-list --count ${_vbump}..HEAD
                        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
                        OUTPUT_VARIABLE RSYNC_PORT_COUNT
                        OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    endif()
endif()

set(RSYNC_VERSION_DISPLAY "${RSYNC_VERSION_STRING}")
if(RSYNC_PORT_REV)
    set(RSYNC_VERSION_DISPLAY "${RSYNC_VERSION_STRING}-g${RSYNC_PORT_REV}")
endif()
if(NOT RSYNC_PORT_COUNT)
    set(RSYNC_PORT_COUNT 0)
endif()

# usage.c prints RSYNC_GITVER when it is defined, so this is what
# `rsync --version` says -- and what the Windows version resource says too.
if(RSYNC_PORT_REV)
    file(WRITE "${GEN_DIR}/git-version.h"
         "#define RSYNC_GITVER \"${RSYNC_VERSION_DISPLAY}\"
")
else()
    file(WRITE "${GEN_DIR}/git-version.h" "")
endif()

# ---------------------------------------------------------------------------
# Version resource (Explorer's Details tab, and what inventory tools read)
# ---------------------------------------------------------------------------
if(WIN32)
    # The strings are the version composed above, so the resource, the release
    # name and `rsync --version` all say the same thing.  The binary
    # FILEVERSION is four 16-bit numbers and cannot hold a rev, so it takes the
    # release from version.h and the build number from the commit count --
    # which advances with every commit here, so Windows can order two builds:
    #
    #   strings   3.5.0-g9273e229
    #   binary    3,5,0,<commits since version.h said 3.5.0>
    set(RSYNC_RC_VERSION_DISPLAY "${RSYNC_VERSION_DISPLAY}")
    # OriginalFilename is defined as the name the file was built under, so it
    # has to follow the rename; the description says which build this is,
    # since the two are otherwise identical in the properties dialog.
    set(RSYNC_RC_ORIGINAL_FILENAME "${RSYNC_EXE_NAME}.exe")
    if(RSYNC_WIN_ARCH STREQUAL "x86")
        set(RSYNC_RC_DESCRIPTION "rsync - fast incremental file transfer (32-bit)")
    else()
        set(RSYNC_RC_DESCRIPTION "rsync - fast incremental file transfer")
    endif()
    set(_maj ${PROJECT_VERSION_MAJOR})
    set(_min ${PROJECT_VERSION_MINOR})
    set(_pat ${PROJECT_VERSION_PATCH})
    set(_twk "${RSYNC_PORT_COUNT}")

    # Four fields, always: an absent component is 0, and building this from a
    # list would drop the empty ones and emit "3,4,3" -- which rc.exe accepts
    # and then reports as a different version than intended.
    foreach(_f _maj _min _pat _twk)
        if(NOT ${_f})
            set(${_f} 0)
        endif()
    endforeach()
    set(RSYNC_RC_FILEVERSION "${_maj},${_min},${_pat},${_twk}")
    # Only the binary field is capped at 16 bits per component.
    if(_twk GREATER 65535)
        message(WARNING "commit count ${_twk} exceeds the 16 bits FILEVERSION "
                        "allows; the build number will wrap")
    endif()

    # Flag anything that is not a plain released version, so the properties
    # dialog says so rather than implying a release build.
    set(RSYNC_RC_FILEFLAGS "0")
    if(RSYNC_RC_VERSION_DISPLAY MATCHES "dev|pre|-g[0-9a-fA-F]+")
        set(RSYNC_RC_FILEFLAGS "VS_FF_PRERELEASE")
    endif()

    configure_file("${CMAKE_CURRENT_SOURCE_DIR}/win32/rsync-version.rc.in"
                   "${GEN_DIR}/rsync-version.rc" @ONLY)
endif()

# ---------------------------------------------------------------------------
# Platform feature detection
# ---------------------------------------------------------------------------
if(NOT WIN32)
    # _GNU_SOURCE et al. must be visible to every probe as well as the build.
    add_compile_definitions(_GNU_SOURCE)
    set(CMAKE_REQUIRED_DEFINITIONS -D_GNU_SOURCE)
endif()

set(CHECK_HEADERS
    ctype.h dirent.h dl.h fcntl.h grp.h iconv.h inttypes.h langinfo.h
    libcharset.h limits.h locale.h malloc.h memory.h ndir.h netdb.h
    netgroup.h stdint.h stdlib.h strings.h string.h utime.h unistd.h
    compat.h bsd/string.h
    netinet/in_systm.h netinet/ip.h
    sys/acl.h sys/attr.h sys/dir.h sys/extattr.h sys/fcntl.h sys/file.h
    sys/filio.h sys/ioctl.h sys/mode.h sys/ndir.h sys/param.h sys/select.h
    sys/socket.h sys/stat.h sys/time.h sys/types.h sys/un.h sys/wait.h
    sys/xattr.h attr/xattr.h linux/falloc.h)

foreach(hdr IN LISTS CHECK_HEADERS)
    string(TOUPPER "HAVE_${hdr}" var)
    string(REGEX REPLACE "[/.-]" "_" var "${var}")
    check_include_file("${hdr}" ${var})
endforeach()

check_c_source_compiles("
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <float.h>
int main(void) { return 0; }" STDC_HEADERS)

set(CHECK_FUNCS
    asprintf chmod fchmod ftruncate getaddrinfo getattrlist getcwd getegid
    geteuid getgrouplist getgroups getpass getpgrp hstrerror inet_ntop
    inet_pton initgroups innetgr lchmod lchown link linkat locale_charset
    lseek64 lutimes mallinfo mallinfo2 memmove mkfifo mkfifoat mknod mknodat
    mkstemp64 mktime nanosleep nl_langinfo open64 putenv readlink setattrlist
    setenv seteuid setgroups setlocale setsid setvbuf sigaction sigprocmask
    snprintf socketpair strchr strerror strlcat strlcpy strpbrk strtol
    tcgetpgrp unsetenv usleep utime utimensat utimes vasprintf vsnprintf
    wait4 waitpid fallocate posix_fallocate)

foreach(fn IN LISTS CHECK_FUNCS)
    string(TOUPPER "HAVE_${fn}" var)
    check_function_exists("${fn}" ${var})
endforeach()

if(HAVE_POSIX_FALLOCATE)
    set(HAVE_EFFICIENT_POSIX_FALLOCATE 1)
endif()
if(HAVE_FALLOCATE)
    set(HAVE_SYS_FALLOCATE 1)
endif()

# MSVC's CRT exports a `setmode` that has nothing to do with BSD setmode(3),
# and an `open64`-free but `_stat64`-based large-file API.  Don't let the
# generic probes above mistake those for the POSIX functions rsync wants.
if(WIN32)
    # MinGW exposes asprintf symbols from libmingwex without declaring them
    # for this C dialect.  Use rsync's portable implementations on every
    # native Windows toolchain instead.
    set(HAVE_ASPRINTF 0)
    set(HAVE_VASPRINTF 0)
    set(HAVE_SETMODE 0)
    set(HAVE_GETCWD 1)
    set(HAVE_CHMOD 1)
    set(HAVE_LCHOWN 1)
    # win32_utimes() can set times on directories (via CreateFile with
    # FILE_FLAG_BACKUP_SEMANTICS); the CRT's _utime() cannot.
    set(HAVE_UTIMES 1)
    set(HAVE_UTIME 1)
    set(HAVE_MKTIME 1)
    set(HAVE_STRCHR 1)
    set(HAVE_STRERROR 1)
    set(HAVE_STRPBRK 1)
    set(HAVE_STRTOL 1)
    set(HAVE_MEMMOVE 1)
    set(HAVE_PUTENV 1)
    set(HAVE_SETVBUF 1)
    set(HAVE_SETLOCALE 1)
    # XP's msvcrt has the older non-C99 formatting semantics.  Keep rsync's
    # well-tested replacements instead of importing newer UCRT aliases.
    set(HAVE_VSNPRINTF 0)
    set(HAVE_SNPRINTF 0)
    set(HAVE_C99_VSNPRINTF 0)
    set(HAVE_ERRNO_DECL 1)
    set(HAVE_STRUCT_UTIMBUF 1)
    set(HAVE_GETADDRINFO 1)
    # InetNtop/InetPton were added after XP.  Build rsync's portable
    # implementations so the executable does not import those newer APIs.
    set(HAVE_INET_NTOP 0)
    set(HAVE_INET_PTON 0)
    set(HAVE_STRUCT_ADDRINFO 1)
    set(HAVE_STRUCT_SOCKADDR_STORAGE 1)
    # The win32 shim implements these on top of the Win32 API.
    set(HAVE_WAITPID 1)
    set(HAVE_SOCKETPAIR 1)
    set(HAVE_USLEEP 1)
    set(HAVE_STRUCT_STAT_ST_RDEV 1)
    # 64-bit file offsets come from the stat64 family, exactly like the
    # large-file path rsync already supports on 32-bit Unix.
    set(HAVE_STRUCT_STAT64 1)
    set(HAVE_LSEEK64 1)
    set(HAVE_OPEN64 1)
    set(HAVE_MKSTEMP64 1)
    set(HAVE_SECURE_MKSTEMP 1)
    set(INET6 1)
    set(SIGNED_CHAR_OK 1)
    set(GETPGRP_VOID 1)
    # Supplied by the CRT or by win32compat.h, so rsync.h must not re-typedef.
    set(HAVE_ID_T 1)
    set(HAVE_PID_T 1)
    set(HAVE_MODE_T 1)
    set(HAVE_OFF_T 1)
    set(HAVE_SIZE_T 1)
    # win32_gettimeofday() takes the (tv, tz) form.
    set(HAVE_GETTIMEOFDAY_TZ 1)
    # win32compat.c implements getpass() with the console API, so the
    # termios-based lib/getpass.c is not needed.
    set(HAVE_GETPASS 1)
    # win32/include supplies these, so rsync.h can include them plainly.
    set(HAVE_DIRENT_H 1)
    # Deliberately NOT setting HAVE_READLINK or HAVE_LINK.  Symlinks and hard
    # links are not a first-class concept for ordinary Windows users --
    # creating a symlink needs Developer Mode or a privilege most accounts
    # lack -- so rsync treats them as the plain files they resolve to and
    # win32/win32links.c lists any it met at the end of the run.
    # _chsize_s() behind the POSIX name; enables --inplace/--append.
    set(HAVE_FTRUNCATE 1)
else()
    check_function_exists(setmode HAVE_SETMODE)
endif()

check_symbol_exists(errno "errno.h" HAVE_ERRNO_DECL)

# --- types -----------------------------------------------------------------
foreach(t id_t mode_t off_t pid_t size_t)
    string(TOUPPER "HAVE_${t}" var)
    check_type_size("${t}" ${var}_SIZE LANGUAGE C)
    if(${var}_SIZE)
        set(${var} 1)
    endif()
endforeach()

check_type_size("long long"     SIZEOF_LONG_LONG)
check_type_size("long double"   SIZEOF_LONG_DOUBLE)
check_type_size("int"           SIZEOF_INT)
check_type_size("short"         SIZEOF_SHORT)
check_type_size("long"          SIZEOF_LONG)
check_type_size("char *"        SIZEOF_CHARP)
check_type_size("time_t"        SIZEOF_TIME_T)
check_type_size("int16_t"       SIZEOF_INT16_T)
check_type_size("uint16_t"      SIZEOF_UINT16_T)
check_type_size("int32_t"       SIZEOF_INT32_T)
check_type_size("uint32_t"      SIZEOF_UINT32_T)
check_type_size("int64_t"       SIZEOF_INT64_T)
check_type_size("off_t"         SIZEOF_OFF_T)

if(SIZEOF_LONG_LONG)
    set(HAVE_LONG_LONG 1)
endif()
if(SIZEOF_LONG_DOUBLE)
    set(HAVE_LONG_DOUBLE 1)
endif()

if(WIN32)
    # MSVC's off_t is a 32-bit long; the 64-bit variant is __int64.
    set(SIZEOF_OFF64_T 8)
else()
    cmake_push_check_state()
    set(CMAKE_EXTRA_INCLUDE_FILES sys/types.h)
    check_type_size("off64_t" SIZEOF_OFF64_T)
    cmake_pop_check_state()
    if(NOT SIZEOF_OFF64_T)
        set(SIZEOF_OFF64_T 0)
    endif()

    cmake_push_check_state()
    set(CMAKE_EXTRA_INCLUDE_FILES sys/types.h sys/stat.h)
    check_type_size("struct stat64" SIZEOF_STRUCT_STAT64)
    cmake_pop_check_state()
    if(SIZEOF_STRUCT_STAT64)
        set(HAVE_STRUCT_STAT64 1)
    endif()
endif()

foreach(v SIZEOF_INT SIZEOF_SHORT SIZEOF_LONG SIZEOF_LONG_LONG SIZEOF_CHARP
          SIZEOF_OFF_T SIZEOF_TIME_T SIZEOF_INT16_T SIZEOF_UINT16_T
          SIZEOF_INT32_T SIZEOF_UINT32_T SIZEOF_INT64_T)
    if(NOT ${v})
        set(${v} 0)
    endif()
endforeach()

# --- struct members --------------------------------------------------------
if(NOT WIN32)
    check_struct_has_member("struct stat" st_rdev "sys/types.h;sys/stat.h"
                            HAVE_STRUCT_STAT_ST_RDEV)
    check_struct_has_member("struct stat" st_mtim.tv_nsec "sys/types.h;sys/stat.h"
                            HAVE_STRUCT_STAT_ST_MTIM_TV_NSEC)
    check_struct_has_member("struct stat" st_mtimensec "sys/types.h;sys/stat.h"
                            HAVE_STRUCT_STAT_ST_MTIMENSEC)
    check_struct_has_member("struct stat" st_mtimespec.tv_nsec "sys/types.h;sys/stat.h"
                            HAVE_STRUCT_STAT_ST_MTIMESPEC_TV_NSEC)
    check_struct_has_member("struct sockaddr_in" sin_len "sys/types.h;netinet/in.h"
                            HAVE_SOCKADDR_IN_LEN)
    check_struct_has_member("struct sockaddr_in6" sin6_scope_id "sys/types.h;netinet/in.h"
                            HAVE_SOCKADDR_IN6_SCOPE_ID)
    check_struct_has_member("struct sockaddr_un" sun_len "sys/types.h;sys/un.h"
                            HAVE_SOCKADDR_UN_LEN)
    check_struct_has_member("struct utimbuf" actime "utime.h"
                            HAVE_STRUCT_UTIMBUF)
    cmake_push_check_state()
    set(CMAKE_EXTRA_INCLUDE_FILES sys/types.h sys/socket.h netdb.h)
    check_type_size("struct addrinfo" SIZEOF_STRUCT_ADDRINFO)
    cmake_pop_check_state()
    if(SIZEOF_STRUCT_ADDRINFO)
        set(HAVE_STRUCT_ADDRINFO 1)
    endif()
    check_symbol_exists(AF_INET6 "sys/types.h;sys/socket.h;netinet/in.h" INET6)

    check_c_source_runs("
#include <sys/types.h>
#include <sys/socket.h>
int main(void) { struct sockaddr_storage x; (void)x; return 0; }"
        HAVE_STRUCT_SOCKADDR_STORAGE)

    # Some old systems take only one argument.
    check_c_source_compiles("
#include <sys/time.h>
int main(void) {
    struct timeval tv; struct timezone tz;
    return gettimeofday(&tv, &tz);
}" HAVE_GETTIMEOFDAY_TZ)

    check_c_source_runs("int main(void) { signed char c = -1; return c > 0; }"
        SIGNED_CHAR_OK)
    check_c_source_compiles("
#include <unistd.h>
int main(void) { return getpgrp() < 0; }" GETPGRP_VOID)
endif()

test_big_endian(WORDS_BIGENDIAN)

check_c_source_compiles("
#include <stdarg.h>
void f(va_list a) { va_list b; va_copy(b, a); va_end(b); }
int main(void) { return 0; }" HAVE_VA_COPY)
check_c_source_compiles("
#include <stdarg.h>
void f(va_list a) { va_list b; __va_copy(b, a); va_end(b); }
int main(void) { return 0; }" HAVE___VA_COPY)

check_symbol_exists(major "sys/sysmacros.h" MAJOR_IN_SYSMACROS)
if(NOT MAJOR_IN_SYSMACROS)
    check_symbol_exists(major "sys/mkdev.h" MAJOR_IN_MKDEV)
endif()

check_symbol_exists(FALLOC_FL_PUNCH_HOLE "linux/falloc.h" HAVE_FALLOC_FL_PUNCH_HOLE)
check_symbol_exists(FALLOC_FL_ZERO_RANGE "linux/falloc.h" HAVE_FALLOC_FL_ZERO_RANGE)

if(HAVE_GETGROUPS)
    set(GETGROUPS_T "gid_t")
else()
    set(GETGROUPS_T "int")
endif()
if(WIN32)
    set(GETGROUPS_T "int")
endif()

if(HAVE_LOCALE_H AND HAVE_SETLOCALE)
    set(CONFIG_LOCALE 1)
endif()

# rsync's flexible-array usage; C99 compilers are fine with it.
set(USE_FLEXIBLE_ARRAY 1)

# --- symlink/hardlink behaviour --------------------------------------------
# The autoconf build runs these as runtime probes.  Assume the common POSIX
# answers when cross-compiling or on Windows.
if(NOT WIN32)
    set(CAN_HARDLINK_SYMLINK 1)
    set(CAN_HARDLINK_SPECIAL 1)
    if(APPLE)
        set(CAN_SET_SYMLINK_TIMES 1)
        set(CAN_CHMOD_SYMLINK 1)
    endif()
    if(HAVE_LUTIMES OR HAVE_UTIMENSAT)
        set(CAN_SET_SYMLINK_TIMES 1)
    endif()
    if(HAVE_LCHOWN)
        set(CAN_CHOWN_SYMLINK 1)
    endif()
    if(HAVE_LCHMOD)
        set(CAN_CHMOD_SYMLINK 1)
    endif()
    if(HAVE_UTIMENSAT OR HAVE_STRUCT_STAT_ST_MTIM_TV_NSEC)
        set(CAN_SET_NSEC 1)
    endif()
    set(MKNOD_CREATES_FIFOS 1)
    set(MKNOD_CREATES_SOCKETS 1)
    if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
        set(NO_SYMLINK_USER_XATTRS 1)
    endif()
endif()

# ---------------------------------------------------------------------------
# Optional third-party features
# ---------------------------------------------------------------------------
set(RSYNC_EXTRA_LIBS "")
if(MINGW)
    # The static msvcrt import archive's snprintf aliases call helpers in
    # mingwex.  Put this pair before GCC's implicit runtime libraries so the
    # one-pass PE linker sees the dependency in resolvable order.
    list(APPEND RSYNC_EXTRA_LIBS msvcrt-os mingwex msvcrt-os mingwex)
endif()

if(RSYNC_ENABLE_ACLS AND HAVE_SYS_ACL_H AND NOT WIN32)
    set(SUPPORT_ACLS 1)
    set(HAVE_POSIX_ACLS 1)
    check_include_file("acl/libacl.h" HAVE_ACL_LIBACL_H)
    find_library(ACL_LIBRARY acl)
    if(ACL_LIBRARY)
        list(APPEND RSYNC_EXTRA_LIBS ${ACL_LIBRARY})
    endif()
    if(APPLE)
        set(HAVE_OSX_ACLS 1)
        unset(HAVE_POSIX_ACLS)
    endif()
endif()

if(RSYNC_ENABLE_XATTRS AND NOT WIN32)
    if(APPLE)
        set(SUPPORT_XATTRS 1)
        set(HAVE_OSX_XATTRS 1)
    elseif(HAVE_SYS_XATTR_H OR HAVE_ATTR_XATTR_H)
        set(SUPPORT_XATTRS 1)
        set(HAVE_LINUX_XATTRS 1)
    elseif(HAVE_SYS_EXTATTR_H)
        set(SUPPORT_XATTRS 1)
        set(HAVE_FREEBSD_XATTRS 1)
    endif()
endif()

if(RSYNC_ENABLE_OPENSSL)
    find_package(OpenSSL REQUIRED)
    set(USE_OPENSSL 1)
    set(SET_OPENSSL_CONF 1)
    list(APPEND RSYNC_EXTRA_LIBS OpenSSL::Crypto)
endif()

if(RSYNC_ENABLE_ZSTD)
    find_package(zstd REQUIRED)
    set(SUPPORT_ZSTD 1)
    list(APPEND RSYNC_EXTRA_LIBS zstd::libzstd)
endif()

if(RSYNC_ENABLE_LZ4)
    find_library(LZ4_LIBRARY lz4 REQUIRED)
    set(SUPPORT_LZ4 1)
    list(APPEND RSYNC_EXTRA_LIBS ${LZ4_LIBRARY})
endif()

if(RSYNC_ENABLE_XXHASH)
    find_library(XXHASH_LIBRARY xxhash REQUIRED)
    set(SUPPORT_XXHASH 1)
    set(SUPPORT_XXH3 1)
    list(APPEND RSYNC_EXTRA_LIBS ${XXHASH_LIBRARY})
endif()

if(RSYNC_ENABLE_ICONV AND HAVE_ICONV_H)
    set(USE_ICONV_OPEN 1)
    set(ICONV_CONST "")
    set(ICONV_OPTION "NULL")
    find_library(ICONV_LIBRARY iconv)
    if(ICONV_LIBRARY)
        list(APPEND RSYNC_EXTRA_LIBS ${ICONV_LIBRARY})
    endif()
endif()

if(RSYNC_EXTERNAL_ZLIB)
    find_package(ZLIB REQUIRED)
    set(EXTERNAL_ZLIB 1)
    list(APPEND RSYNC_EXTRA_LIBS ZLIB::ZLIB)
endif()

# ---------------------------------------------------------------------------
# config.h
# ---------------------------------------------------------------------------
configure_file(cmake/config.h.in "${GEN_DIR}/config.h")

# ---------------------------------------------------------------------------
# rounding.h -- probe for the padding the compiler puts in file_extras[]
# ---------------------------------------------------------------------------
set(EXTRA_ROUNDING_FOUND OFF)
foreach(r 0 1 3)
    # The bindir argument is what makes this the older try_compile signature.
    # Dropping it is neater, but that form arrived in CMake 3.25 and this file
    # claims to work with 3.20 -- Ubuntu 22.04 ships 3.22, where the shorter
    # form reads "SOURCES" as the bindir and fails somewhere else entirely.
    try_compile(ROUNDING_OK "${CMAKE_CURRENT_BINARY_DIR}/CMakeFiles/rounding-try"
        SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/rounding.c"
        COMPILE_DEFINITIONS "-DEXTRA_ROUNDING=${r}"
        C_STANDARD 99
        CMAKE_FLAGS "-DINCLUDE_DIRECTORIES=${GEN_DIR};${CMAKE_CURRENT_SOURCE_DIR};${RSYNC_PLATFORM_INCLUDE_DIR}"
        OUTPUT_VARIABLE ROUNDING_OUTPUT)
    if(ROUNDING_OK)
        file(WRITE "${GEN_DIR}/rounding.h" "#define EXTRA_ROUNDING ${r}\n")
        message(STATUS "EXTRA_ROUNDING is ${r}")
        set(EXTRA_ROUNDING_FOUND ON)
        break()
    endif()
    unset(ROUNDING_OK CACHE)
endforeach()
if(NOT EXTRA_ROUNDING_FOUND)
    message(FATAL_ERROR
        "Failed to determine EXTRA_ROUNDING (could not build rounding.c):\n"
        "${ROUNDING_OUTPUT}")
endif()

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
set(RSYNC_SOURCES
    flist.c rsync.c generator.c receiver.c cleanup.c sender.c exclude.c
    util1.c util2.c main.c checksum.c match.c syscall.c log.c
    backup.c delete.c
    options.c io.c compat.c hlink.c token.c uidlist.c socket.c hashtable.c
    usage.c fileio.c batch.c clientname.c chmod.c acls.c xattrs.c
    progress.c
    params.c loadparm.c clientserver.c access.c connection.c authenticate.c)

set(RSYNC_LIB_SOURCES
    lib/wildmatch.c lib/compat.c lib/snprintf.c lib/mdfour.c lib/md5.c
    lib/permstring.c lib/pool_alloc.c lib/sysacls.c lib/sysxattrs.c)

if(NOT HAVE_GETADDRINFO OR NOT HAVE_STRUCT_ADDRINFO)
    list(APPEND RSYNC_LIB_SOURCES lib/getaddrinfo.c)
endif()
if(NOT HAVE_INET_NTOP)
    list(APPEND RSYNC_LIB_SOURCES lib/inet_ntop.c)
endif()
if(NOT HAVE_INET_PTON)
    list(APPEND RSYNC_LIB_SOURCES lib/inet_pton.c)
endif()
if(NOT HAVE_GETPASS)
    list(APPEND RSYNC_LIB_SOURCES lib/getpass.c)
endif()

set(POPT_SOURCES
    popt/popt.c popt/poptconfig.c popt/popthelp.c popt/poptparse.c
    popt/poptint.c)

set(ZLIB_SOURCES
    zlib/deflate.c zlib/inffast.c zlib/inflate.c zlib/inftrees.c
    zlib/trees.c zlib/zutil.c zlib/adler32.c zlib/compress.c zlib/crc32.c)

# pipe.c and win32/win32pipe.c are alternative implementations of the same
# interface (process plumbing + the generator/receiver split), so exactly
# one of them is linked.
if(WIN32)
    list(APPEND RSYNC_SOURCES win32/win32pipe.c)
else()
    list(APPEND RSYNC_SOURCES pipe.c)
endif()

set(RSYNC_ALL_SOURCES ${RSYNC_SOURCES} ${RSYNC_LIB_SOURCES} ${POPT_SOURCES})
if(NOT RSYNC_EXTERNAL_ZLIB)
    list(APPEND RSYNC_ALL_SOURCES ${ZLIB_SOURCES})
endif()

if(WIN32)
    enable_language(RC)   # for win32/rsync.rc
    list(APPEND RSYNC_ALL_SOURCES
        win32/win32compat.c
        win32/win32proc.c
        win32/win32io.c
        win32/win32dir.c
        win32/win32fork.c
        win32/win32args.c
        win32/win32links.c
        win32/win32harden.c)

endif()

if(RSYNC_ENABLE_SIMD AND CMAKE_SIZEOF_VOID_P EQUAL 8
   AND CMAKE_SYSTEM_PROCESSOR MATCHES "x86_64|AMD64")
    enable_language(CXX)
    set(CMAKE_CXX_STANDARD 17)
    set(USE_ROLL_SIMD 1)
    list(APPEND RSYNC_ALL_SOURCES simd-checksum-x86_64.cpp)
endif()

add_executable(rsync ${RSYNC_ALL_SOURCES})

target_include_directories(rsync PRIVATE
    "${RSYNC_PLATFORM_INCLUDE_DIR}"   # a port's stand-ins for missing headers
    "${GEN_DIR}"                      # -I. : generated headers win
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/popt")

if(NOT RSYNC_EXTERNAL_ZLIB)
    target_include_directories(rsync PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/zlib")
endif()

# popt/system.h (and others) only pull in config.h when this is set; the
# autoconf build passes it in CFLAGS for every translation unit.
target_compile_definitions(rsync PRIVATE HAVE_CONFIG_H)

target_link_libraries(rsync PRIVATE ${RSYNC_EXTRA_LIBS})

if(WIN32)
    # rsync.exe for x64, rsync-x86.exe for 32-bit.  Everything that consumes
    # the binary goes through $<TARGET_FILE:rsync>, so this is the only place
    # the name is decided.
    set_target_properties(rsync PROPERTIES OUTPUT_NAME "${RSYNC_EXE_NAME}")

    target_link_libraries(rsync PRIVATE ws2_32 advapi32 shell32 userenv)
    target_compile_definitions(rsync PRIVATE
        _CRT_SECURE_NO_WARNINGS
        _CRT_NONSTDC_NO_WARNINGS
        _WINSOCK_DEPRECATED_NO_WARNINGS
        WINVER=0x0501
        _WIN32_WINNT=0x0501
        RSYNC_XP_TARGET=1
        WIN32_LEAN_AND_MEAN
        NOMINMAX)
    if(MSVC)
        # Keep the noise down: rsync is old-school C and trips a lot of
        # MSVC's signed/unsigned and deprecation warnings.
        target_compile_options(rsync PRIVATE /W2 /wd4996 /wd4267 /wd4244 /wd4018)
        # Embed the manifest (UTF-8 active code page) as a resource, and
        # turn off the linker's own manifest so there is exactly one.  The
        # version resource is separate and goes on rsync.exe alone: the test
        # helpers want the same manifest but must not claim to be rsync.
        target_sources(rsync PRIVATE win32/rsync.rc "${GEN_DIR}/rsync-version.rc")
        target_link_options(rsync PRIVATE "/MANIFEST:NO")
    endif()
endif()

if(NOT WIN32)
    find_library(M_LIBRARY m)
    if(M_LIBRARY)
        target_link_libraries(rsync PRIVATE ${M_LIBRARY})
    endif()
endif()

# ---------------------------------------------------------------------------
# Test helpers
#
# runtests.py refuses to start without these; they are the C parts of the
# suite (Makefile.in's CHECK_PROGS).  Small enough to build unconditionally.
# ---------------------------------------------------------------------------
set(HELPER_WIN32_SOURCES "")
if(WIN32)
    # The compat layer these need; win32pipe.c and win32fork.c are left out
    # because they reach into main.c/io.c, which no helper links.
    set(HELPER_WIN32_SOURCES
        win32/win32compat.c win32/win32io.c win32/win32dir.c
        win32/win32proc.c win32/win32links.c win32/win32harden.c)
endif()

function(rsync_test_helper name)
    set(_helper_sources ${ARGN} ${HELPER_WIN32_SOURCES})

    if(WIN32)
        list(FIND _helper_sources "lib/snprintf.c" _have_snprintf)
        if(_have_snprintf EQUAL -1)
            list(APPEND _helper_sources lib/snprintf.c)
        endif()
    endif()

    add_executable(${name} ${_helper_sources})
    target_include_directories(${name} PRIVATE
        "${RSYNC_PLATFORM_INCLUDE_DIR}"
        "${GEN_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}"
        "${CMAKE_CURRENT_SOURCE_DIR}/popt")
    target_compile_definitions(${name} PRIVATE HAVE_CONFIG_H)
    target_link_libraries(${name} PRIVATE ${RSYNC_EXTRA_LIBS})
    # Land beside rsync itself: runtests.py looks for them in one place.
    set_target_properties(${name} PROPERTIES
        RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}")
    if(WIN32)
        target_link_libraries(${name} PRIVATE ws2_32 advapi32 shell32 userenv)
        target_compile_definitions(${name} PRIVATE
            _CRT_SECURE_NO_WARNINGS _CRT_NONSTDC_NO_WARNINGS
            _WINSOCK_DEPRECATED_NO_WARNINGS WINVER=0x0501
            _WIN32_WINNT=0x0501 RSYNC_XP_TARGET=1
            WIN32_LEAN_AND_MEAN NOMINMAX)
        if(MSVC)
            target_compile_options(${name} PRIVATE
                /W2 /wd4996 /wd4267 /wd4244 /wd4018)
            # Same UTF-8 code page as rsync, so they agree about file names.
            target_sources(${name} PRIVATE win32/rsync.rc)
            target_link_options(${name} PRIVATE "/MANIFEST:NO")
        endif()
    endif()
endfunction()

set(HELPER_COMMON syscall.c util2.c t_stub.c
                  lib/compat.c lib/snprintf.c lib/permstring.c)

# Makefile.in links tls and trimslash against syscall.c but not util1.c.
# That works where AT_FDCWD exists, because syscall.c then compiles the
# openat()-based secure_relative_open(); Windows has no AT_FDCWD, so it takes
# the portable fallback instead, which calls pathjoin() from util1.c.  util1.c
# in turn wants wildmatch and the log-level arrays that those two helpers --
# unlike the other three -- do not declare themselves.
set(HELPER_PATHJOIN "")
if(WIN32)
    set(HELPER_PATHJOIN util1.c lib/wildmatch.c win32/win32helperstubs.c)
endif()

# tls prints a Unix-style listing -- mode bits, uid/gid and a symlink's
# target -- and calls do_readlink() unconditionally, which this build does
# not provide because it has no symlink support.  It is only needed by
# testsuite/runtests.py, which does not run on Windows; win32/tests/ compares
# trees directly instead.
if(NOT WIN32)
    rsync_test_helper(tls    tls.c ${HELPER_COMMON} ${HELPER_PATHJOIN}
                             lib/sysxattrs.c ${POPT_SOURCES})
endif()
rsync_test_helper(trimslash  trimslash.c ${HELPER_COMMON} ${HELPER_PATHJOIN})
rsync_test_helper(t_unsafe   t_unsafe.c ${HELPER_COMMON}
                             util1.c lib/wildmatch.c)
rsync_test_helper(t_chmod_secure  t_chmod_secure.c ${HELPER_COMMON}
                             util1.c lib/wildmatch.c)
rsync_test_helper(t_secure_relpath t_secure_relpath.c ${HELPER_COMMON}
                             util1.c lib/wildmatch.c)

# New in rsync 3.5.0, and all pure logic -- no symlinks, no dirfds, no daemon --
# so they run here exactly as they do on Unix.  Link lines follow Makefile.in's
# T_*_OBJ.  t_acl, t_symlink_secure and t_rename_secure are deliberately absent:
# each is about ACLs or the symlink-TOCTOU primitive, neither of which this port
# has.  t_safe_arg needs main.c with main() renamed out, which the CMake build
# has no equivalent of yet.
rsync_test_helper(t_clean_fname t_clean_fname.c ${HELPER_COMMON}
                             util1.c lib/wildmatch.c)
# Not HELPER_COMMON: that carries syscall.c, whose globals this helper does not
# define (upstream's T_HASHTABLE_OVERFLOW_OBJ leaves it out for the same reason).
rsync_test_helper(t_hashtable_overflow t_hashtable_overflow.c hashtable.c
                             util2.c t_stub.c lib/compat.c lib/snprintf.c
                             lib/wildmatch.c)
rsync_test_helper(t_iwildmatch t_iwildmatch.c lib/wildmatch.c)
# No lib/wildmatch.c here: wildtest.c #includes it directly.
rsync_test_helper(wildtest   wildtest.c lib/compat.c lib/snprintf.c
                             ${POPT_SOURCES})
rsync_test_helper(getgroups  getgroups.c)
rsync_test_helper(getfsdev   getfsdev.c)

# ---------------------------------------------------------------------------
# The Windows test suite
#
# testsuite/ assumes Unix semantics throughout, so win32/tests/ covers what
# this port supports instead and skips what Windows genuinely cannot do:
#
#     cmake --build build --target win32-tests
#
# Set RSYNC_WIN_TEST_HOST=user@host first to include the ssh transfer tests.
# ---------------------------------------------------------------------------
if(WIN32)
    add_custom_target(win32-tests
        COMMAND ${Python3_EXECUTABLE}
                "${CMAKE_CURRENT_SOURCE_DIR}/win32/tests/run.py"
                --rsync-bin $<TARGET_FILE:rsync>
                --tooldir  $<TARGET_FILE_DIR:rsync>
                --srcdir   "${CMAKE_CURRENT_SOURCE_DIR}"
        DEPENDS rsync trimslash t_unsafe t_chmod_secure t_secure_relpath
                wildtest getgroups getfsdev
        COMMENT "Running the Windows test suite"
        USES_TERMINAL)
endif()

install(TARGETS rsync RUNTIME DESTINATION bin)

message(STATUS "")
message(STATUS "rsync ${RSYNC_VERSION_DISPLAY} configuration:")
if(RSYNC_PORT_REV)
    message(STATUS "  built from .......... ${RSYNC_PORT_REV}, "
                   "+${RSYNC_PORT_COUNT} commits since version.h said ${RSYNC_VERSION_STRING}")
else()
    message(STATUS "  upstream base ....... unknown (no master ref to compare against)")
endif()
message(STATUS "  system .............. ${CMAKE_SYSTEM_NAME}")
if(WIN32)
    message(STATUS "  target .............. ${RSYNC_WIN_ARCH} -> ${RSYNC_EXE_NAME}.exe")
endif()
message(STATUS "  compiler ............ ${CMAKE_C_COMPILER_ID} ${CMAKE_C_COMPILER_VERSION}")
message(STATUS "  ACLs ................ ${SUPPORT_ACLS}")
message(STATUS "  xattrs .............. ${SUPPORT_XATTRS}")
message(STATUS "  OpenSSL ............. ${USE_OPENSSL}")
message(STATUS "  bundled zlib ........ ${RSYNC_EXTERNAL_ZLIB}")
if(MSVC)
    message(STATUS "  static CRT .......... ${RSYNC_STATIC_CRT}")
    message(STATUS "  mitigations ......... ${RSYNC_HARDEN}")
    message(STATUS "  strict mitigations .. ${RSYNC_STRICT_MITIGATIONS}")
endif()
message(STATUS "  AddressSanitizer .... ${RSYNC_ENABLE_ASAN}")
message(STATUS "")
