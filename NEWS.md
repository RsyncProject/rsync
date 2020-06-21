# NEWS for rsync 3.2.1 (UNRELEASED)

Protocol: 31 (unchanged)

## Changes since 3.2.0:

### BUG FIXES:

 - Fixed a build issue with the MD5 assembly-language code by removing some
   advanced direcives.

 - Use the preprocessor with the asm file to ensure that if the code is
   unneeded, it doesn't get built.

 - Make sure that the asm code doesn't make the stack get set to executable.

 - Avoid some build issues with the SIMD code, including avoiding a clang++
   core dump when `-g` is combined with `-O2`.

 - Fix an issue with the md2man code when building in an external dir.

### ENHANCEMENTS:

 - Added "atimes" to the capabilities list that `--version` outputs.

 - Mention either "default protect-args" or "optional protect-args" in the
   `--version` capabilities depending on how rsync was configured.

 - Some info on optimizations was elided from the `--version` capabilities
   since they aren't really user-facing capabilities.  You can get the info
   back (plus the status of a couple extra optimizations) by repeating the
   `--version` option (e.g. `-VV`).

------------------------------------------------------------------------------
