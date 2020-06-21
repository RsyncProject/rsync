# NEWS for rsync 3.2.1 (UNRELEASED)

Protocol: 31 (unchanged)

## Changes since 3.2.0:

### BUG FIXES:

 - Fixed a build issue with the MD5 assembly-language code by removing some
   non-portable directives.

 - Use the preprocessor with the asm file to ensure that if the code is
   unneeded, it doesn't get built.

 - Avoid the stack getting set to executable when including the asm code.

 - Avoid some build issues with the SIMD code, including avoiding a clang++
   core dump when `-g` is combined with `-O2`.

 - Fix an issue with the md2man code when building in an external dir.

 - Disable --atimes on macOS (it apparently doesn't work).

### ENHANCEMENTS:

 - Added `--early-input=FILE` option that allows the client to send some
   data to the "early exec" daemon script on its stdin.

 - Added "atimes" to the capabilities list that `--version` outputs.

 - Mention either "default protect-args" or "optional protect-args" in the
   `--version` capabilities depending on how rsync was configured.

 - Some info on optimizations was elided from the `--version` capabilities
   since they aren't really user-facing capabilities.  You can get the info
   back (plus the status of a couple extra optimizations) by repeating the
   `--version` option (e.g. `-VV`).

 - Updated various documented links to be https instead of http.

### PACKAGING RELATED:

 - If you had to use --disable-simd for 3.2.0, you should be able to remove
   that and let it auto-disable.

 - The MD5 asm code is now under its own configure flag (not shared with the
   SIMD setting), so if you have any issues compiling it, re-run configure with
   `--disable-asm`.

------------------------------------------------------------------------------
