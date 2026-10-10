#!/usr/bin/env python3

import os
import subprocess

from harness.rsync import plant_operator_symlink, rsync_argv, run_symlink_matrix

timestamp = 1_234_567_890

def check(option):
    def case(context):
        source = context.base / 'source'
        destination = context.base / 'destination'
        source.mkdir()
        destination.mkdir()
        source_file = source / 'file'
        source_file.write_text('source00\n' if option == 'copy' else 'matching\n')
        argument, outside = plant_operator_symlink(context, destination)
        outside.mkdir(parents=True, exist_ok=True)
        outside_file = outside / 'file'
        outside_file.write_text('outside0\n' if option == 'copy' else 'matching\n')
        os.utime(source_file, (timestamp, timestamp))
        os.utime(outside_file, (timestamp, timestamp))
        args = ['-a', f'--{option}-dest={argument}']
        if context.insecure:
            args.append('--insecure-links')
        subprocess.run(rsync_argv(*args, 'source/', 'destination/'), cwd=context.base,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = destination / 'file'
        if option == 'compare':
            return not result.exists()
        if option == 'copy':
            return result.exists() and result.read_text() == 'outside0\n'
        return result.exists() and result.stat().st_ino == outside_file.stat().st_ino

    run_symlink_matrix(f'--{option}-dest', case)

for option in ('compare', 'copy', 'link'):
    check(option)

print('alternate-destination symlink policy enforced')
