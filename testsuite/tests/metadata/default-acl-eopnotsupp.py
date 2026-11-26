#!/usr/bin/env python3

import subprocess

from harness.environment import SCRATCHDIR
from harness.filesystem import rmtree
from harness.mutation import build_patched_rsync
from harness.results import test_fail
from harness.rsync import rsync_argv_for
from harness import metadata

metadata(features={'acl', 'native_build'}, transports={'pipe'},
         mutates={'filesystem', 'process'}, tags={'metadata', 'regression'})

PATCH_FUNCTION = 'int default_perms_for_dir(const char *dir)'
PATCH_FUNCTION_NEW = '''#undef ENOTSUP
#define ENOTSUP 12346
#undef EOPNOTSUPP
#define EOPNOTSUPP 12345

int default_perms_for_dir(const char *dir)'''
PATCH_CALL = '\tsacl = sys_acl_get_file(dir, SMB_ACL_TYPE_DEFAULT);'
PATCH_CALL_NEW = '\terrno = EOPNOTSUPP;\n\tsacl = NULL;'

probe_rsync = build_patched_rsync('default-acl-eopnotsupp-rsync', [
    ('acls.c', PATCH_FUNCTION, PATCH_FUNCTION_NEW),
    ('acls.c', PATCH_CALL, PATCH_CALL_NEW),
])

base = SCRATCHDIR / 'default-acl-eopnotsupp'
src = base / 'src'
dest = base / 'dest'
rmtree(base)
(src / 'dir').mkdir(parents=True)
(src / 'dir' / 'file').write_text('content\n')

result = subprocess.run(
    rsync_argv_for(probe_rsync, '-r', str(src) + '/', str(dest) + '/'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
copied = dest / 'dir' / 'file'
if result.returncode != 0 or not copied.is_file() or copied.read_text() != 'content\n':
    test_fail(f'transfer failed after unsupported default ACL: {result.stderr!r}')
if b'default_perms_for_dir' in result.stderr:
    test_fail(f'EOPNOTSUPP was reported as an ACL failure: {result.stderr!r}')

print('default-acl-eopnotsupp: unsupported default ACL falls back to the umask')
