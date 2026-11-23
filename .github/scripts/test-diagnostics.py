from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import call, patch


diagnostics = runpy.run_path(str(Path(__file__).with_name('diagnostics.py')))


class Diagnostics(unittest.TestCase):
    def test_helper_runner_covers_the_roster(self):
        with tempfile.TemporaryDirectory(prefix='rsync-helpers-') as directory:
            root = Path(directory)
            for name in ('test-z.py', 'test-a.py', 'support.py'):
                (root / name).touch()
            with patch.object(diagnostics['subprocess'], 'call', side_effect=[0, 17]) as run, redirect_stdout(StringIO()):
                self.assertEqual(diagnostics['check_helpers'](root), 17)
            self.assertEqual(run.call_args_list, [call([sys.executable, '-B', '-S', str(root / name)])
                                                  for name in ('test-a.py', 'test-z.py')])

    def test_helper_runner_rejects_an_empty_roster(self):
        with tempfile.TemporaryDirectory(prefix='rsync-helpers-') as directory:
            with self.assertRaisesRegex(ValueError, 'No CI helper tests'):
                diagnostics['check_helpers'](Path(directory))

    def test_shellcheck_discovers_tracked_scripts_and_propagates_failure(self):
        with tempfile.TemporaryDirectory(prefix='rsync-diagnostics-') as directory:
            root = Path(directory)
            files = {'--shell helper': '#! /bin/sh -e\n',
                     'backup.txt': '#!/usr/bin/env -S bash -e\n',
                     'template.sh.in': '# shellcheck shell=sh\n',
                     'example.md': 'Example:\n#!/bin/sh\n',
                     'other': '#!/usr/bin/env python3\n',
                     'untracked.sh': '#!/bin/sh\n'}
            for name, contents in files.items():
                (root / name).write_text(contents)
            tracked = b'--shell helper\0backup.txt\0template.sh.in\0example.md\0other\0'
            process = diagnostics['subprocess']
            with patch.object(process, 'check_output', return_value=tracked) as list_files, \
                 patch.object(process, 'call', return_value=17) as lint, redirect_stdout(StringIO()):
                self.assertEqual(diagnostics['check_shell_scripts'](root), 17)
            list_files.assert_called_once_with(['git', 'ls-files', '-z'], cwd=root)
            lint.assert_called_once_with(['shellcheck', '--', '--shell helper', 'backup.txt',
                                          'template.sh.in'], cwd=root)

    def test_logged_failure(self):
        with tempfile.TemporaryDirectory(prefix='rsync-diagnostics-') as directory:
            log = Path(directory) / 'run.log'
            output = StringIO()
            with redirect_stdout(output):
                status = diagnostics['logged']([sys.executable, '-S', '-c',
                                                 'print("retained output"); raise SystemExit(17)'], log)
            self.assertEqual(status, 17)
            self.assertEqual(log.read_text(), 'retained output\n')
            self.assertEqual(output.getvalue(), log.read_text())

    def test_valgrind_results(self):
        with tempfile.TemporaryDirectory(prefix='rsync-diagnostics-') as directory:
            root = Path(directory)
            output = root / 'scratch' / 'testtmp' / 'valgrind-logs'
            output.mkdir(parents=True)
            previous = Path.cwd()
            os.chdir(root)
            try:
                argv = ['diagnostics.py', 'valgrind-results', '--privilege', 'non-root', '--transport', 'pipe']
                with patch.dict(os.environ, {'VALGRIND_SCRATCH': str(root)}), \
                     patch.object(sys, 'argv', argv), redirect_stdout(StringIO()):
                    with self.assertRaisesRegex(ValueError, 'No Valgrind logs'):
                        diagnostics['main']()
                    path = output / 'valgrind.1.log'
                    path.write_text('ERROR SUMMARY: 0 errors\n')
                    self.assertEqual(diagnostics['main'](), 0)
                    self.assertEqual((root / 'test-results/valgrind-logs' / path.name).read_text(), path.read_text())
                    path.write_text('ERROR SUMMARY: 2 errors\n')
                    with self.assertRaisesRegex(ValueError, 'Valgrind reported errors'):
                        diagnostics['main']()
            finally:
                os.chdir(previous)

    def test_summary(self):
        with tempfile.TemporaryDirectory(prefix='rsync-diagnostics-') as directory:
            root = Path(directory)
            log = root / 'build.log'
            log.write_text('compiler output\nlines: 50%\n')
            summary = root / 'summary.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}), redirect_stdout(StringIO()):
                diagnostics['summary']('Coverage', log, r'^lines:')
            self.assertIn('lines: 50%', summary.read_text())
            self.assertNotIn('compiler output', summary.read_text())

    def test_scan_flags_and_exit_status(self):
        for status in (0, 17):
            with self.subTest(status=status):
                calls = []
                def logged(command, path):
                    calls.append(command)
                    return status
                argv = ['diagnostics.py', 'scan', '23']
                with patch.object(sys, 'argv', argv), patch.dict(diagnostics['main'].__globals__,
                        {'logged': logged, 'summary': lambda *args: None}):
                    self.assertEqual(diagnostics['main'](), status)
                self.assertEqual(calls[0][:2], ['intercept-build-23', 'scan-build-23'])
                self.assertIn('--status-bugs', calls[0])
                self.assertIn('check-progs', calls[0])

    def test_cppcheck_uses_the_configured_database_and_propagates_failure(self):
        build = Path.cwd() / 'build'
        with patch.object(diagnostics['subprocess'], 'call', return_value=17) as run:
            self.assertEqual(diagnostics['check_cppcheck'](build), 17)
        command = run.call_args[0][0]
        self.assertEqual(command[0], 'cppcheck')
        self.assertIn('--project=' + str(build / 'compile_commands.json'), command)
        self.assertIn('-i' + str(build / 'rounding.c'), command)
        self.assertIn('--library=' + str(Path(__file__).resolve().with_name('cppcheck.cfg')), command)
        self.assertIn('--enable=warning,performance,portability,missingInclude', command)
        self.assertIn('--error-exitcode=1', command)
        self.assertEqual(run.call_args[1]['cwd'], build.parent)


if __name__ == '__main__':
    unittest.main()
