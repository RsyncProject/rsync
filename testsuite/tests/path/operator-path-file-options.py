#!/usr/bin/env python3

import subprocess

from harness.rsync import plant_operator_symlink, rsync_argv, run_symlink_matrix

def check(option):
    def case(context):
        source = context.base / 'source'
        destination = context.base / 'destination'
        source.mkdir()
        destination.mkdir()
        (source / 'file').write_text('data\n')
        argument, outside = plant_operator_symlink(context, context.base, kind='file')
        args = ['-a']
        if option == 'files-from':
            outside.write_text('file\n')
            args.append(f'--files-from={argument}')
        elif option == 'log-file':
            args.extend(('-i', f'--log-file={argument}'))
        else:
            args.append(f'--write-batch={argument}')
        if context.insecure:
            args.append('--insecure-links')
        subprocess.run(rsync_argv(*args, 'source/', 'destination/'), cwd=context.base,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if option == 'files-from':
            return (destination / 'file').exists()
        return outside.is_file() and (option != 'log-file' or outside.stat().st_size > 0)

    run_symlink_matrix(f'--{option}', case)

for option in ('files-from', 'log-file', 'write-batch'):
    check(option)

print('file option symlink policy enforced')
