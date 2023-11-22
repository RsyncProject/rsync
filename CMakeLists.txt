project(rsync)

add_custom_command(
    OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/rsync
    COMMAND ./configure --disable-md2man CPPFLAGS=-I/opt/homebrew/include LDFLAGS=-L/opt/homebrew/lib
    COMMAND make
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
)

add_custom_target(
    rsync_build ALL
    DEPENDS ${CMAKE_CURRENT_BINARY_DIR}/rsync
)

add_executable(rsync IMPORTED)
set_target_properties(rsync PROPERTIES IMPORTED_LOCATION 
${CMAKE_CURRENT_BINARY_DIR}/rsync)
