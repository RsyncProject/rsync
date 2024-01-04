project(rsync)

message(STATUS "arch info: ${CMAKE_HOST_SYSTEM_PROCESSOR}")

if(CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "arm64")
    set(CPP_FLAGS "-I${OPENSSL_ROOT_DIR}/include -I/opt/homebrew/include")
    set(LD_FLAGS "-L${OPENSSL_ROOT_DIR}/lib -L/opt/homebrew/lib")
elseif(CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "x86_64")
    set(CPP_FLAGS "-I${OPENSSL_ROOT_DIR}/include -I/usr/include")
    set(LD_FLAGS "-L${OPENSSL_ROOT_DIR}/lib -L/usr/lib")
else()
    message(FATAL_ERROR "Unsupported architecture: ${CMAKE_HOST_SYSTEM_PROCESSOR}")
endif()

add_custom_command(
    OUTPUT ${CMAKE_CURRENT_SOURCE_DIR}/rsync
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/configure --disable-md2man --with-included-popt --with-included-zlib --disable-xxhash --disable-zstd --disable-lz4 --disable-openssl CPPFLAGS=${CPP_FLAGS} LDFLAGS=${LD_FLAGS}
    COMMAND make
    WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
    COMMENT "Configuring and building rsync for ${CMAKE_HOST_SYSTEM_PROCESSOR}"
    VERBATIM
)

add_custom_target(
    rsync_build ALL
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/rsync
)

add_executable(rsync IMPORTED GLOBAL)
set_target_properties(rsync PROPERTIES IMPORTED_LOCATION ${CMAKE_CURRENT_BINARY_DIR}/rsync)
