# rsync Style Guide

This document is the authoritative reference for all coding style and formatting
decisions in the rsync project. All contributors — whether submitting a first
patch or a major feature — are expected to read and follow this guide.

Style is enforced automatically via `clang-format` where possible. This guide
explains the *why* behind each rule, covers areas `clang-format` cannot reach,
and documents the handful of directory-level exceptions that apply to vendored
libraries.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Indentation](#2-indentation)
3. [Line Length and Wrapping](#3-line-length-and-wrapping)
4. [Brace Placement](#4-brace-placement)
5. [Spacing](#5-spacing)
6. [Naming Conventions](#6-naming-conventions)
7. [Functions](#7-functions)
8. [Comments](#8-comments)
9. [Headers and Include Guards](#9-headers-and-include-guards)
10. [Macros and Preprocessor Directives](#10-macros-and-preprocessor-directives)
11. [Types, Structs, and Typedefs](#11-types-structs-and-typedefs)
12. [Pointers](#12-pointers)
13. [Control Flow](#13-control-flow)
14. [Error Handling and Return Values](#14-error-handling-and-return-values)
15. [Memory Management](#15-memory-management)
16. [Vendored Library Exceptions](#16-vendored-library-exceptions)
17. [Using clang-format](#17-using-clang-format)
18. [Manual Interventions](#18-manual-interventions)
19. [Commit and Patch Standards](#19-commit-and-patch-standards)

---

## 1. Overview

rsync follows the **Linux Kernel (K&R) Coding Style**. The key principles behind
this style are:

- **Readability at a glance.** Code is read far more often than it is written.
  Consistent style reduces the cognitive overhead of switching between files.
- **Respect for history.** rsync has a long history. New code should feel at
  home next to old code.
- **Minimal surprise.** Developers familiar with the Linux kernel or other major
  C projects should feel immediately comfortable in this codebase.

Where this guide and the output of `clang-format` disagree, `clang-format` wins
for mechanical matters (whitespace, braces). For semantic matters (naming,
structure, comments), this guide is authoritative.

---

## 2. Indentation

### Use Hard Tabs

Indentation uses **8-character hardware tabs**. Do not use spaces for base
indentation. This is non-negotiable for native rsync source files (`.c` and `.h`).

> **Notation used in whitespace-visible examples throughout this section:**
> `→` represents one tab character; `·` represents one space character.
> These symbols appear after code blocks to make invisible byte-level
> differences explicit, since tabs and spaces look identical when rendered.

```c
/* CORRECT */
int foo(void)
{
	if (condition) {
		do_something();
		do_something_else();
	}
}
```

```c
/* WRONG — spaces used instead of tabs */
int foo(void)
{
    if (condition) {
        do_something();
        do_something_else();
    }
}
```

The two blocks above render identically in most editors and viewers, but differ
at the byte level. The whitespace-visible representation makes the distinction
unambiguous:

```text
CORRECT — each level is ONE → (tab) character:
{
→if (condition) {
→→do_something();
→→do_something_else();
→}
}

WRONG — each level is FOUR · (space) characters:
{
····if (condition) {
········do_something();
········do_something_else();
····}
}
```

### Why 8-Space Tabs?

The 8-character tab width is an intentional forcing function: deeply nested code
becomes visually wide, encouraging developers to break it into well-named helper
functions rather than pushing complexity inward. If your indentation is consuming
half the line, your function is probably too complex.

### Continuation Lines

When a statement wraps to the next line, the continuation is indented with a
**single extra tab** beyond the opening line's indentation level. Do not align
continuation tokens with characters on the line above.

```c
/* CORRECT */
if (some_long_condition &&
	another_condition &&
	yet_another_condition) {
	do_work();
}
```

```c
/* WRONG — aligned with opening parenthesis */
if (some_long_condition &&
    another_condition &&
    yet_another_condition) {
	do_work();
}
```

The CORRECT continuation uses a tab; the WRONG continuation uses spaces to
visually align with the character after the opening parenthesis. Both can look
identical at certain tab-width settings:

```text
CORRECT — continuation indented with one → (tab):
if (some_long_condition &&
→another_condition &&
→yet_another_condition) {
→do_work();
}

WRONG — continuation space-aligned to opening parenthesis with ····:
if (some_long_condition &&
····another_condition &&
····yet_another_condition) {
→do_work();
}
```

Note that in the WRONG version, `do_work()` still uses a tab (it is inside
the block). Only the continuation lines use the incorrect space-alignment.

---

## 3. Line Length and Wrapping

### 80-Column Limit

Lines must not exceed **80 characters**. This limit applies to code, comments,
and string literals alike.

To check compliance without reformatting:

```bash
grep -n '.\{81\}' file.c
```

### Wrapping Long Expressions

When an expression, condition, or argument list exceeds 80 columns, break it
before a binary operator or after a comma, and indent the continuation by one
tab.

```c
/* Long function call — break after commas */
result = some_function(argument_one, argument_two,
	argument_three, argument_four);

/* Long condition — break before logical operator */
if (transfer_size > MAX_BLOCK_SIZE &&
	!(flags & FLAG_PARTIAL) &&
	checksum_valid(buf, len)) {
	flush_buffer();
}
```

### Wrapping Long String Literals

Prefer breaking adjacent string literals across lines rather than producing a
single overlong string. The compiler will concatenate them automatically.

```c
/* CORRECT */
rprintf(FINFO,
	"rsync: [sender] write error: "
	"connection closed\n");

/* WRONG */
rprintf(FINFO, "rsync: [sender] write error: connection closed\n");
```

---

## 4. Brace Placement

rsync follows K&R brace placement, with one important distinction between
function definitions and all other compound statements.

### Control Statements

Opening braces for `if`, `else`, `for`, `while`, `do`, and `switch` go on the
**same line** as the keyword, separated by a single space.

```c
if (condition) {
	foo();
} else if (other) {
	bar();
} else {
	baz();
}

for (i = 0; i < count; i++) {
	process(i);
}

while (!done) {
	tick();
}
```

### Function Definitions

The opening brace of a **function body** goes on a **new line**, alone.

```c
/* CORRECT */
int calculate_transfer_size(int size)
{
	if (size > 0) {
		return size * 2;
	}
	return 0;
}
```

```c
/* WRONG — opening brace on same line as function header */
int calculate_transfer_size(int size) {
	return size * 2;
}
```

### Single-Statement Bodies

Omitting braces around a single-statement body is permitted, but only when the
body fits on one line and reads unambiguously. When in doubt, add the braces.
Never omit braces on `if/else` chains where one branch requires braces.

```c
/* Permitted */
if (!buf)
	return -1;

/* WRONG — dangling else ambiguity; add braces */
if (a)
	if (b)
		foo();
else	/* This else binds to the inner if, not the outer */
	bar();
```

### `do...while` Loops

The `while` condition of a `do...while` loop goes on the same line as the
closing brace.

```c
do {
	chunk = read_data(buf, len);
	len -= chunk;
} while (len > 0);
```

---

## 5. Spacing

### After Control Keywords

A **single space** must follow control keywords: `if`, `else`, `for`, `while`,
`do`, `switch`, `return`, `sizeof`, `typeof`, `alignof`.

```c
if (x)          /* CORRECT */
if(x)           /* WRONG */

sizeof(int)     /* CORRECT */
sizeof (int)    /* WRONG */
```

### Around Binary Operators

Spaces are required on **both sides** of binary operators: arithmetic,
relational, logical, bitwise, and assignment.

```c
/* CORRECT */
size = what * 8 + modestr;
if (a == b || c != d)

/* WRONG */
size = what*8+modestr;
if (a==b||c!=d)
```

### No Space Before Function Call Parenthesis

Do not place a space between a function name and its opening parenthesis.

```c
foo(args);      /* CORRECT */
foo (args);     /* WRONG */
```

This rule does **not** apply to control keywords (see above), which are not
function calls.

### No Padding Inside Parentheses

Do not add spaces on the inside of parentheses in expressions, function calls,
or function declarations.

```c
foo(a, b, c)     /* CORRECT */
foo( a, b, c )   /* WRONG */

if (x > 0)       /* CORRECT */
if ( x > 0 )     /* WRONG */
```

### Pointer Spacing

The `*` in a pointer declaration attaches to the **variable name**, not the
type. See [Section 12](#12-pointers) for the full pointer style rules.

```c
char *buf;       /* CORRECT */
char* buf;       /* WRONG */
```

### No Trailing Whitespace

Lines must not end with trailing spaces or tabs. Configure your editor to strip
trailing whitespace on save. The CI pipeline will reject patches with trailing
whitespace.

### Blank Lines

- Use **one** blank line to separate logically distinct blocks within a function.
- Use **two** blank lines to separate top-level declarations (functions, global
  variable blocks).
- Do not use more than two consecutive blank lines anywhere.

---

## 6. Naming Conventions

### General Principle: Lowercase with Underscores

All identifiers — variables, functions, struct members, and file names — use
lowercase letters with words separated by underscores. There is no camelCase or
PascalCase in rsync C code.

```c
int file_count;
void send_file_list(struct file_list *flist);
struct filter_rule_list;
```

### Variables

- Local variables should have short, descriptive names. Single-letter names are
  acceptable for loop counters (`i`, `j`, `k`) and similarly conventional uses.
- Global variables must have descriptive, unambiguous names. Prefix module-level
  globals with a short module identifier if there is any risk of collision.
- Boolean-intent variables should read naturally in a condition:
  `if (preserve_times)`, not `if (times_flag == 1)`.

```c
/* Good local variable names */
int i, len, fd;
char *path, *dest;
struct file_struct *file;

/* Good global names */
int verbose;
int preserve_hard_links;
int io_error;
```

### Functions

Function names describe what the function **does**, using a verb or
verb-noun form.

```c
void send_file_list(struct file_list *flist);
int  read_int(int f);
void maybe_send_keepalive(time_t now, int flags);
```

Avoid generic names like `process()` or `handle()` without a qualifying noun.

### Constants and Macros

Preprocessor constants and macros are written in **ALL_CAPS_WITH_UNDERSCORES**.

```c
#define MAX_PATH_LENGTH  1024
#define CHUNK_SIZE       4096
#define FLAG_SEND_DONE   (1 << 0)
```

Macro names that act like functions may use lowercase if they are designed as
drop-in replacements for real functions, but this is rare and must be clearly
documented.

### Struct and Type Names

Struct tags and `typedef`-introduced type names use lowercase with underscores,
consistent with variable naming. See [Section 11](#11-types-structs-and-typedefs)
for more detail.

---

## 7. Functions

### Return Type on the Same Line

The return type must appear on the **same line** as the function name. Do not
split the return type onto its own line (GNU style).

```c
/* CORRECT */
int  read_int(int f);
void send_file_list(struct file_list *flist);
static int compare_devs(const void *a, const void *b);

/* WRONG — GNU style; return type on its own line */
int
read_int(int f);
```

### No Space Before the Parameter List

Do not pad the inside of the parameter list parentheses with spaces.

```c
void foo(int a, char *b)   /* CORRECT */
void foo( int a, char *b ) /* WRONG */
```

### Keep Functions Short and Focused

A function should do **one thing** and do it well. As a rough guideline, if a
function body does not fit on a single screen (~50 lines), consider whether it
can be decomposed into smaller, well-named helpers.

Heavy nesting (more than three levels) is a strong signal that a block should
become its own function.

### Static Functions

Helper functions used only within a single `.c` file should be declared
`static`. This reduces the global symbol namespace and gives the compiler more
freedom to inline or optimize.

```c
static int compute_checksum(const char *buf, size_t len);
```

### Parameter Ordering

Follow the convention: output/destination parameters first, then
input/source parameters, then flags or options. Consistency within a module
matters more than a universal rule.

---

## 8. Comments

### Style

rsync uses **block comments** (`/* ... */`) for all substantive documentation.
C99 line comments (`//`) are **not** used, even though the codebase is compiled
as C99+. This maintains stylistic uniformity with the project's long history.

```c
/* CORRECT — block comment */

// WRONG — C99 line comment
```

### Function-Level Comments

Place a comment above a function to describe its purpose, any non-obvious
parameters, and what it returns. The comment is not required to repeat
information that is self-evident from the function name and signature alone.

```c
/*
 * Attempt to open a file for reading, retrying on EINTR.  Returns the file
 * descriptor on success, or -1 with errno set on failure.
 */
int robust_open(const char *path, int flags, mode_t mode)
{
	...
}
```

### Inline Comments

Inline comments explain **why**, not **what**. Code should be clear enough that
a "what" comment is redundant. Reserve inline comments for non-obvious decisions,
algorithmic tricks, or work-arounds for external bugs.

```c
/* Skip the first two bytes; they are a legacy framing artifact from
 * the pre-2.0 protocol and carry no useful information. */
buf += 2;

/* Use signed arithmetic here: file sizes can be negative in error paths. */
if ((off_t)len < 0)
	return -1;
```

### TODO and FIXME

Mark known problems or deferred work with a `FIXME` or `TODO` tag so they can
be found with a simple `grep`.

```c
/* FIXME: this assumes a 4-byte int; see issue #42. */
/* TODO: replace with a hash table once the list grows beyond a few hundred entries. */
```

### Commenting Out Code

Do not commit large blocks of commented-out code. If the code may be needed
again, note the reason in the commit message and remove it from the source tree;
version control preserves the history.

---

## 9. Headers and Include Guards

### Include Guard Format

Every header file must be protected against multiple inclusion using a
traditional preprocessor guard. The guard macro is named after the file path,
uppercased, with non-alphanumeric characters replaced by underscores.

```c
#ifndef RSYNC_PROTO_H
#define RSYNC_PROTO_H

/* ... declarations ... */

#endif /* RSYNC_PROTO_H */
```

The `#endif` must include the guard macro name in a comment.

Do **not** use `#pragma once`. It is not standard C and is not used in rsync.

### Include Order

Group `#include` directives in this order, with a blank line between groups:

1. The companion header for the current `.c` file (if applicable).
2. Standard C library headers (`<stdio.h>`, `<stdlib.h>`, etc.).
3. POSIX and system headers (`<unistd.h>`, `<sys/types.h>`, etc.).
4. rsync project-internal headers.

```c
#include "rsync.h"

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include <sys/stat.h>
#include <sys/types.h>

#include "proto.h"
#include "io.h"
```

### What Belongs in a Header

A header file should contain only:

- `#include` directives for types the header itself requires.
- Type definitions (`struct`, `enum`, `typedef`).
- `extern` variable declarations (not definitions).
- Function prototypes.
- Preprocessor constants and macros.

Do **not** place function or variable definitions in a header file unless they
are `static inline`.

---

## 10. Macros and Preprocessor Directives

### Macros That Span Multiple Lines

Use a backslash continuation and align the backslashes in the rightmost column
for readability.

```c
#define COPY_FILE_DATA(src, dst, len)		\
	do {					\
		memcpy((dst), (src), (len));	\
		(dst) += (len);			\
		(src) += (len);			\
	} while (0)
```

### Wrap Multi-Statement Macros in `do { ... } while (0)`

Any macro that expands to more than one statement **must** be wrapped in a
`do { ... } while (0)` block. This ensures it behaves correctly in all
syntactic contexts, including as the body of an `if` without braces.

```c
/* CORRECT */
#define CHECK_AND_LOG(x)	\
	do {			\
		if (!(x))	\
			log_error(#x);	\
	} while (0)

/* WRONG — breaks if used without braces */
#define CHECK_AND_LOG(x)	\
	if (!(x))		\
		log_error(#x);
```

### Parenthesize Macro Parameters

Always parenthesize macro parameters in the expansion body to prevent
operator-precedence surprises.

```c
#define MAX(a, b)  ((a) > (b) ? (a) : (b))   /* CORRECT */
#define MAX(a, b)   (a > b ? a : b)           /* WRONG */
```

### Preprocessor Indentation

When nesting `#if` / `#ifdef` / `#else` / `#endif` directives, indent the
directives themselves (using spaces, after the `#`) to reflect the nesting
depth. This applies only to deeply nested blocks.

```c
#ifdef SUPPORT_XATTRS
#  ifdef HAVE_SYS_XATTR_H
#    include <sys/xattr.h>
#  endif
#endif
```

---

## 11. Types, Structs, and Typedefs

### Structs

Define structs with a tag. Members are indented with one tab.

```c
struct file_struct {
	time_t  modtime;
	off_t   length;
	mode_t  mode;
	uid_t   uid;
	gid_t   gid;
	char   *basename;
};
```

Align member names vertically when a group of related members have similar types
and it aids readability, but do not do so mechanically across an entire struct.

### Typedefs

Use `typedef` sparingly. In general, prefer referring to a struct by its full
`struct tag_name` form. Reserve `typedef` for:

- Integer types that abstract over platform-specific widths (e.g., `int32_t`).
- Opaque handle types where callers should never inspect the internals.
- Function pointer types that appear frequently in signatures.

Do **not** typedef a struct merely to avoid writing `struct`:

```c
/* Acceptable */
typedef int64_t file_offset;

/* Avoid — just use struct filter_rule */
typedef struct filter_rule filter_rule_t;
```

### Enums

Use enums for logically related integer constants in preference to a series of
`#define` values. Enum member names follow the ALL_CAPS convention.

```c
enum log_format_action {
	LFA_STRING,
	LFA_FILE,
	LFA_LENGTH,
	LFA_OWNER,
};
```

### Using `int` vs Explicitly Sized Types

Use plain `int`, `long`, `char`, etc. when the exact width does not matter and
portability to the C abstract machine is acceptable. Use `<stdint.h>` types
(`int32_t`, `uint64_t`) when a specific bit width is required for protocol
correctness or serialization.

---

## 12. Pointers

### `*` Attaches to the Variable Name

In declarations, the `*` that qualifies a pointer type belongs with the variable
name, not with the type. This is consistent with how C parses declarations and
avoids the well-known trap of `char* a, b;` (where only `a` is a pointer).

```c
char *buf;            /* CORRECT */
char* buf;            /* WRONG */
char * buf;           /* WRONG */

int *foo, *bar;       /* CORRECT — both are pointers */
int* foo, bar;        /* WRONG — easy to misread bar as a pointer */
```

In function prototypes, the same rule applies to return types and parameters:

```c
char *get_basename(const char *path);   /* CORRECT */
char* get_basename(const char* path);   /* WRONG */
```

### NULL vs 0

Use `NULL` for null pointers. Use `0` for zero integers. Do not use `NULL` as
an integer constant.

---

## 13. Control Flow

### `switch` and `case` Alignment

`case` labels are **not** indented relative to their `switch` statement. They
align vertically with the `switch` keyword. The body of each case is indented
one level from the `case` label.

```c
switch (mode) {
case S_IFREG:
	handle_regular_file();
	break;
case S_IFDIR:
	handle_directory();
	break;
case S_IFLNK:
	handle_symlink();
	break;
default:
	rprintf(FERROR, "unknown mode\n");
	break;
}
```

### Fall-Through in `switch`

Intentional fall-through from one `case` to the next must be marked with a
comment. Unmarked fall-through is a source of bugs and will be treated as an
error during review.

```c
switch (action) {
case ACTION_DELETE:
	delete_file(path);
	/* fall through */
case ACTION_LOG:
	log_action(path);
	break;
}
```

### Avoid `goto`

`goto` is not generally used in rsync. The rare legitimate use is unwinding
multiple resource acquisitions in a single function, where it produces cleaner
code than deeply nested conditionals. If used, the label must clearly describe
its purpose (e.g., `out_free:`, `error_close:`), and the `goto` must jump
**forward** only.

### Early Returns

Prefer early returns to reduce nesting depth. Validate preconditions and return
immediately, leaving the main function body at the top indentation level.

```c
/* PREFERRED — flat, readable */
int send_data(struct file_struct *file, int fd)
{
	if (!file)
		return -1;
	if (fd < 0)
		return -1;

	/* main logic at top level */
	...
}

/* AVOID — unnecessary nesting */
int send_data(struct file_struct *file, int fd)
{
	if (file) {
		if (fd >= 0) {
			/* main logic buried two levels deep */
		}
	}
	return 0;
}
```

---

## 14. Error Handling and Return Values

### Consistent Return Conventions

Functions that can fail should return a value that clearly indicates success or
failure:

- Functions returning a resource (file descriptor, pointer) return the resource
  on success and `-1` or `NULL` on failure, with `errno` set.
- Functions with no meaningful return value that can encounter errors return `0`
  on success and `-1` on failure.
- Boolean-intent functions return non-zero for true/success, zero for
  false/failure.

Document any deviation from these conventions in the function comment.

### Always Check Return Values

Do not silently discard return values from functions that can fail, including
standard library functions. If a return value is intentionally unused, cast it
to `(void)`.

```c
/* CORRECT */
if (write(fd, buf, len) < 0) {
	rsyserr(FERROR, errno, "write to %s failed", path);
	return -1;
}

/* Intentionally discarded */
(void)close(fd);
```

### `errno` Usage

Check `errno` immediately after the failing call; do not allow any other
function call between the failure and the `errno` check, as it may be
overwritten.

---

## 15. Memory Management

### Every Allocation Must Have a Free Path

For every call to `malloc`, `calloc`, `realloc`, or rsync's wrappers (such as
`new_array`), there must be a clear and reachable code path that frees the
memory. Document ownership when passing a pointer between functions.

### Prefer rsync's Allocation Wrappers

rsync provides wrappers around standard allocation functions that automatically
call `out_of_memory()` on failure, removing the need for repetitive null checks
at every call site. Prefer these over raw `malloc`.

```c
/* Prefer */
p = new_array(char, len + 1);

/* Over */
p = malloc(len + 1);
if (!p)
	out_of_memory("context");
```

### Zero-Initialize When in Doubt

Prefer `calloc` (or `memset` after `malloc`) for structs and arrays that will
be partially filled. Uninitialized memory reads are a class of bug that is
difficult to reproduce and debug.

### Do Not Access Memory After Free

Set a pointer to `NULL` after freeing it if there is any chance it might be
dereferenced again in the same scope.

```c
free(buf);
buf = NULL;
```

---

## 16. Vendored Library Exceptions

The rsync repository contains two vendored upstream libraries that follow
slightly different historical formatting conventions. To respect their upstream
style and make it straightforward to apply upstream patches, directory-level
`.clang-format` override files are maintained for each.

### `popt/` and `zlib/` Directories

These directories inherit all Linux K&R structural rules (brace placement,
spacing, naming) **except** for indentation:

| Rule | Main rsync source | `popt/` and `zlib/` |
|---|---|---|
| Indentation | 8-character hard tabs | 4 spaces |
| Brace placement | K&R | K&R |
| Column limit | 80 | 80 |
| Pointer style | `char *p` | `char *p` |

The following pair illustrates what each style looks like in practice. The
rendered indentation width may appear similar depending on your editor's
tab-stop setting — the whitespace-visible block below each example makes the
actual characters unambiguous.

```c
/* rsync native source (e.g. io.c) — hard tab indentation */
int read_int(int f)
{
	int x;
	if (f < 0) {
		return -1;
	}
	return x;
}
```

```c
/* popt/ or zlib/ — 4-space indentation */
int poptReadDefaultConfig(poptContext con, int flags)
{
    int rc;
    if (!con) {
        return 0;
    }
    return rc;
}
```

```text
Native rsync source — ONE → (tab) per level:
{
→int x;
→if (f < 0) {
→→return -1;
→}
→return x;
}

popt/ and zlib/ — FOUR · (spaces) per level:
{
····int rc;
····if (!con) {
········return 0;
····}
····return rc;
}
```

The local `_clang-format` files in those directories instruct `clang-format` to
apply the correct indentation automatically. You do not need to manage this
manually.

### General Rule for Vendored Code

Do not reformat vendored files purely for style. Changes to `popt/` and `zlib/`
should be limited to:

- Applying patches from upstream.
- Bug fixes that cannot wait for an upstream release.
- Security patches.

If such a change is required, preserve the existing indentation style of the
surrounding code. Do not mix formatting changes with functional changes in the
same commit.

---

## 17. Using clang-format

All formatting of native rsync `.c` and `.h` files is managed by `clang-format`.
The configuration lives in the `.clang-format` file at the repository root, with
directory-level overrides in `popt/` and `zlib/`.

### Formatting Files In-Place

To automatically apply the rules and rewrite files in place, use the `-i` flag.

Format the entire repository:

```bash
find . -name "*.c" -o -name "*.h" | xargs clang-format -i
```

Format a single file:

```bash
clang-format -i path/to/file.c
```

### Validating Without Modifying (Dry-Run)

To check whether files comply without altering them — the recommended approach
for pre-commit hooks and CI pipelines — use `--dry-run --Werror`. This writes
any violations to standard error and exits with a non-zero status if any
formatting issues are found.

```bash
find . -name "*.c" -o -name "*.h" | xargs clang-format --dry-run --Werror
```

A zero exit code means all checked files are compliant.

### Recommended Pre-Commit Hook

Add the following to `.git/hooks/pre-commit` to block non-compliant commits:

```bash
#!/bin/sh
set -e
FILES=$(git diff --cached --name-only --diff-filter=ACM | grep -E '\.(c|h)$')
if [ -n "$FILES" ]; then
	echo "$FILES" | xargs clang-format --dry-run --Werror
fi
```

Make the hook executable:

```bash
chmod +x .git/hooks/pre-commit
```

### Editor Integration

Most editors can invoke `clang-format` automatically on save or on demand:

- **Vim**: Install `vim-clang-format` and bind it to a key.
- **Emacs**: Use `clang-format.el`, available in `llvm/utils/`.
- **VS Code**: Install the `C/C++` extension; set `"C_Cpp.clang_format_style": "file"`.

---

## 18. Manual Interventions

`clang-format` handles the vast majority of mechanical formatting, but there are
a small number of legacy patterns it cannot fix automatically without breaking
standard C syntax. These must be corrected by hand.

### Math Operator Spacing

Legacy code with no spaces around arithmetic operators will trigger `clang-format`
warnings. Add spaces manually before committing.

```c
/* WRONG — legacy style */
size = what*8+modestr;

/* CORRECT */
size = what * 8 + modestr;
```

### Empty `for` Loop Initializations

Some older code has extra spacing inside the empty initializer of a `for` loop.
Collapse this manually.

```c
/* WRONG */
for ( ; condition; increment) { ... }

/* CORRECT */
for (; condition; increment) { ... }
```

### Return Types in Headers

Headers written in GNU style occasionally place the return type on its own line.
Pull the return type onto the same line as the function name to comply with K&R.

```c
/* WRONG — GNU style */
int
send_file_list(struct file_list *flist);

/* CORRECT — K&R style */
int send_file_list(struct file_list *flist);
```

---

## 19. Commit and Patch Standards

### One Logical Change per Commit

Each commit should represent a single, self-contained logical change. Do not
bundle formatting fixes, refactors, and new features into one commit. Reviewers
and future `git blame` users will thank you.

### Style-Only Commits

If you are fixing formatting in existing code, do so in a **dedicated commit**
that contains no functional changes. Label such commits clearly:

```
style: apply clang-format to io.c and proto.c
```

This makes it trivial to exclude style commits from `git log` or `git blame`
when investigating a bug.

### Commit Message Format

Follow the conventional short-description style used throughout the project:

```
subsystem: short description in imperative mood (max 72 chars)

Optional longer explanation of *why* this change was made, any
non-obvious consequences, or references to related issues.
```

Examples of good first lines:

```
sender: fix off-by-one in file list encoding
io: check return value of full_write
generator: avoid double-free when receiver exits early
```

### Submitting Patches

Patches should be generated with `git format-patch` and submitted to the
mailing list or as a pull request. Ensure your patches apply cleanly against
the current `HEAD` of the `master` branch.

Before sending, verify the full formatting check passes:

```bash
find . -name "*.c" -o -name "*.h" | xargs clang-format --dry-run --Werror
```

---

*This style guide is a living document. Corrections and clarifications are
welcome. Submit a patch to update the guide just as you would any other change
to the repository.*
