# NEWS for rsync 3.2.1 (UNRELEASED)

Protocol: 31 (unchanged)

## Changes since 3.2.0:

### BUG FIXES:

 - Fixed a build issue with the MD5 assembly-language code by removing some
   advanced direcives and using the preprocessor to ensure that if the code is
   unneeded, it doesn't get built.

 - Make sure that the asm code doesn't make the stack get set to executable.

 - Avoid some build issues with the SIMD code, including avoiding a clang++
   core dump when `-g` is combined with `-O2`.

 - Fix an issue with the md2man code when building is an external dir.

### ENHANCEMENTS:

 - None.

------------------------------------------------------------------------------
