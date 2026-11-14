from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch


diagnostics = runpy.run_path(str(Path(__file__).with_name('diagnostics.py')))


class Diagnostics(unittest.TestCase):
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
                argv = ['diagnostics.py', 'scan']
                with patch.object(sys, 'argv', argv), patch.dict(diagnostics['main'].__globals__,
                        {'logged': logged, 'summary': lambda *args: None}):
                    self.assertEqual(diagnostics['main'](), status)
                self.assertEqual(calls[0][0], 'scan-build-21')
                self.assertIn('--status-bugs', calls[0])


if __name__ == '__main__':
    unittest.main()
