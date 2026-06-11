cmake_minimum_required(VERSION 3.24)
cmake_policy(SET CMP0135 NEW)
project(rsync C CXX)

# Find dependencies
find_package(ZLIB QUIET)
if(NOT ZLIB_FOUND)
    message(STATUS 'ZLIB not found in environment, fetching via FetchContent')
    include(FetchContent)
    FetchContent_Declare(
        zlib
        GIT_REPOSITORY https://github.com/madler/zlib.git
        GIT_TAG v1.3.1
    )
    FetchContent_MakeAvailable(zlib)
    if(NOT TARGET ZLIB::ZLIB)
        add_library(ZLIB::ZLIB ALIAS zlibstatic)
    endif()
endif()
find_package(zstd CONFIG REQUIRED)
find_package(lz4 CONFIG REQUIRED)
find_package(xxHash CONFIG REQUIRED)
find_package(OpenSSL REQUIRED)

find_path(POPT_INCLUDE_DIR NAMES popt.h)
find_library(POPT_LIBRARY NAMES popt)

# Source files
set(RSYNC_SOURCES
    flist.c rsync.c generator.c receiver.c cleanup.c sender.c exclude.c
    util1.c util2.c main.c checksum.c match.c syscall.c log.c backup.c delete.c
    options.c io.c compat.c hlink.c token.c uidlist.c socket.c hashtable.c
    usage.c fileio.c batch.c clientname.c chmod.c acls.c xattrs.c
    progress.c pipe.c
    params.c loadparm.c clientserver.c access.c connection.c authenticate.c
    lib/wildmatch.c lib/compat.c lib/snprintf.c lib/mdfour.c lib/md5.c
    lib/permstring.c lib/pool_alloc.c lib/sysacls.c lib/sysxattrs.c
)

# Placeholder config.h to avoid compiler errors on Windows where configure wasn't run
set(HAVE_CONFIG_H 1)
set(RSYNC_VERSION "3.3.0")
set(HAVE_ZLIB 1)
set(HAVE_OPENSSL 1)
set(HAVE_STRERROR 1)
set(PACKAGE "rsync")
set(POPT_SYSCONFDIR "/etc")

set(SIZEOF_INT 4)
set(SIZEOF_LONG 4)
set(SIZEOF_SHORT 2)
set(SIZEOF_INT32_T 4)
set(SIZEOF_UINT32_T 4)
set(SIZEOF_INT64_T 8)
set(SIZEOF_UINT64_T 8)
set(SIZEOF_OFF_T 4)
set(SIZEOF_TIME_T 8)
set(SIZEOF_CHARP 8)

set(HAVE_SIZE_T 1)
set(HAVE_OFF_T 1)
set(HAVE_TIME_T 1)
set(HAVE_PID_T 1)
set(HAVE_DIRENT_H 1)
set(HAVE_UTIME 1)
set(HAVE_UTIMES 1)
set(HAVE_STRPBRK 1)
set(HAVE_GETCWD 1)

set(HAVE_STRUCT_ADDRINFO 1)
set(HAVE_STRUCT_SOCKADDR_STORAGE 1)
set(HAVE_GETADDRINFO 1)
set(HAVE_GETNAMEINFO 1)
set(HAVE_SOCKADDR_IN6 1)
set(HAVE_MEMMOVE 1)
set(HAVE_GETTIMEOFDAY_TZ 1)
set(HAVE_READLINK 1)

set(SIGUSR1 10)
set(SIGUSR2 12)
set(SIGHUP 1)
set(SIGALRM 14)
set(WNOHANG 1)
set(O_NOFOLLOW 0)
set(O_DIRECTORY 0)
set(AT_REMOVEDIR 0x200)

set(RSYNC_RSH "ssh")
set(NOBODY_USER "nobody")
set(NOBODY_GROUP "nobody")
set(RSYNCD_SYSCONF "/etc/rsyncd.conf")

configure_file(${CMAKE_CURRENT_SOURCE_DIR}/config.h.in ${CMAKE_CURRENT_BINARY_DIR}/config.h)

# Use the pre-generated headers that are committed to the repo
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/proto.h ${CMAKE_CURRENT_BINARY_DIR}/proto.h COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/daemon-parm.h ${CMAKE_CURRENT_BINARY_DIR}/daemon-parm.h COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/help-rsync.h ${CMAKE_CURRENT_BINARY_DIR}/help-rsync.h COPYONLY)
configure_file(${CMAKE_CURRENT_SOURCE_DIR}/help-rsyncd.h ${CMAKE_CURRENT_BINARY_DIR}/help-rsyncd.h COPYONLY)

file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/default-cvsignore.h "#define DEFAULT_CVSIGNORE \".svn .git .hg .bzr .cvs\"\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/default-dont-compress.h "#define DEFAULT_DONT_COMPRESS \"*.gz *.zip *.z *.rpm *.deb *.iso *.bz2 *.tgz *.tbz *.rar *.jar *.pdf *.7z *.xz *.lzma\"\n")

add_executable(rsync ${RSYNC_SOURCES})

# Target include directories
get_filename_component(AUTO_WIN_MSVC_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc" ABSOLUTE)
include(FetchContent)
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc")
        FetchContent_Declare(auto_win_msvc SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../auto-win-msvc")
else()
    FetchContent_Declare(auto_win_msvc GIT_REPOSITORY https://github.com/SamuelMarks/auto-win-msvc.git GIT_TAG master)
endif()
FetchContent_MakeAvailable(auto_win_msvc)

target_include_directories(rsync PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)


# Link libraries
target_link_libraries(rsync PRIVATE
    auto-win-msvc
    ws2_32
    netapi32
    ZLIB::ZLIB
    zstd::libzstd
    lz4::lz4
    xxHash::xxhash
    OpenSSL::Crypto
    OpenSSL::SSL
)

if(POPT_INCLUDE_DIR AND POPT_LIBRARY)
    target_link_libraries(rsync PRIVATE ${POPT_LIBRARY})
    target_include_directories(rsync PRIVATE ${POPT_INCLUDE_DIR})
else()
    # Build included popt as fallback
    add_library(popt STATIC 
        popt/popt.c popt/poptconfig.c popt/popthelp.c popt/poptparse.c popt/poptint.c
    )
    # Exclude CMAKE_CURRENT_SOURCE_DIR to avoid io.h clash
    target_include_directories(popt PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/popt)
    target_link_libraries(popt PRIVATE auto-win-msvc)
    target_link_libraries(rsync PRIVATE popt)
    target_include_directories(rsync PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/popt)
endif()

# Test Helpers
add_executable(tls tls.c syscall.c util2.c t_stub.c lib/compat.c lib/snprintf.c lib/permstring.c lib/sysxattrs.c)
target_link_libraries(tls PRIVATE auto-win-msvc ws2_32 netapi32 popt)
target_include_directories(tls PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(trimslash trimslash.c syscall.c util2.c t_stub.c lib/compat.c lib/snprintf.c)
target_link_libraries(trimslash PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(trimslash PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(t_unsafe t_unsafe.c syscall.c util1.c util2.c t_stub.c lib/compat.c lib/snprintf.c lib/wildmatch.c)
target_link_libraries(t_unsafe PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(t_unsafe PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(t_chmod_secure t_chmod_secure.c syscall.c util1.c util2.c t_stub.c lib/compat.c lib/snprintf.c lib/wildmatch.c lib/permstring.c)
target_link_libraries(t_chmod_secure PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(t_chmod_secure PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(t_secure_relpath t_secure_relpath.c syscall.c util1.c util2.c t_stub.c lib/compat.c lib/snprintf.c lib/wildmatch.c lib/permstring.c)
target_link_libraries(t_secure_relpath PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(t_secure_relpath PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(wildtest wildtest.c t_stub.c lib/compat.c lib/snprintf.c)
target_link_libraries(wildtest PRIVATE auto-win-msvc ws2_32 netapi32 popt)
target_include_directories(wildtest PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(getgroups getgroups.c)
target_link_libraries(getgroups PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(getgroups PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

add_executable(getfsdev getfsdev.c)
target_link_libraries(getfsdev PRIVATE auto-win-msvc ws2_32 netapi32)
target_include_directories(getfsdev PRIVATE ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/lib ${CMAKE_CURRENT_SOURCE_DIR}/popt)

# Tests
enable_testing()
find_program(PYTHON_EXECUTABLE python)
if(PYTHON_EXECUTABLE)
    file(GLOB TEST_SCRIPTS "${CMAKE_CURRENT_SOURCE_DIR}/testsuite/*_test.py")
    foreach(TEST_SCRIPT IN LISTS TEST_SCRIPTS)
        get_filename_component(TEST_NAME ${TEST_SCRIPT} NAME)
        add_test(
            NAME ${TEST_NAME}
            COMMAND ${PYTHON_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/runtests.py --rsync-bin $<TARGET_FILE:rsync> ${TEST_SCRIPT}
            WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
        )
        # Ensure test uses the helpers built here
        set_tests_properties(${TEST_NAME} PROPERTIES ENVIRONMENT "TOOLDIR=$<TARGET_FILE_DIR:tls>")
    endforeach()
endif()

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")

if(TARGET popt)
  target_compile_definitions(popt PRIVATE HAVE_CONFIG_H=1)
endif()
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/rounding.h "#define EXTRA_ROUNDING 0\n")
file(WRITE ${CMAKE_CURRENT_BINARY_DIR}/git-version.h "#define RSYNC_GITOBJ \"\"\n")
