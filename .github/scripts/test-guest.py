from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


guest = runpy.run_path(str(Path(__file__).with_name('guest.py')))


class Guest(unittest.TestCase):
    def run_guest(self, platform, failure=None):
        with tempfile.TemporaryDirectory(prefix='rsync-guest-') as directory:
            output = Path(directory) / 'results'
            calls = []
            account = SimpleNamespace(pw_name='rsynctest', pw_uid=1000, pw_gid=1000, pw_dir='/home/rsynctest')
            def process(command, **kwargs):
                code = 1 if failure and failure(command) else 0
                calls.append((command, kwargs))
                return SimpleNamespace(wait=lambda: code, poll=lambda: code)
            argv = ['guest.py', platform, '--release', '11.0', '--output', str(output)]
            system = {'freebsd': 'FreeBSD', 'netbsd': 'NetBSD', 'openbsd': 'OpenBSD', 'solaris': 'SunOS'}[platform]
            with patch.object(sys, 'argv', argv), patch('platform.system', return_value=system), \
                 patch('platform.release', return_value='11.0'), patch('platform.machine', return_value='amd64'), \
                 patch('pwd.getpwnam', return_value=account), patch('os.getgrouplist', return_value=[1000, 1001]), \
                 patch('os.chown'), patch('subprocess.Popen', side_effect=process), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=0)), \
                 redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                result = guest['main']()
            return result, (output / 'status').read_text().strip(), calls

    def test_native_passes(self):
        for platform in ('freebsd', 'netbsd', 'openbsd', 'solaris'):
            with self.subTest(platform=platform):
                result, status, calls = self.run_guest(platform)
                self.assertEqual((result, status), (0, '0'))
                tests = [(command, options) for command, options in calls if 'testsuite/tools/check.py' in command]
                self.assertEqual(len(tests), 2 if platform == 'solaris' else 4)
                for command, options in tests:
                    if 'nonroot' in command[5].split(','):
                        self.assertEqual(options['user'], 1000)
                        self.assertEqual(options['group'], 1000)
                        self.assertEqual(options['extra_groups'], [1000, 1001])
                        self.assertEqual(options['env']['HOME'], '/home/rsynctest')
                    else:
                        self.assertNotIn('user', options)
                if platform == 'openbsd':
                    self.assertTrue(all('-j2' in command for command, _ in tests if '--use-tcp' in command))
                if platform in ('netbsd', 'solaris'):
                    configure = next(command for command, _ in calls if command[0] == './configure')
                    self.assertIn('--disable-zstd', configure)
                    self.assertIn('--disable-idn', configure)

    def test_failed_pass_does_not_hide_remaining_results(self):
        result, status, calls = self.run_guest('netbsd', lambda command: 'testsuite/tools/check.py' in command)
        self.assertEqual((result, status), (0, '1'))
        self.assertEqual(sum('testsuite/tools/check.py' in command for command, _ in calls), 4)

    def test_failed_build_stops_tests(self):
        result, status, calls = self.run_guest('netbsd', lambda command: command[:2] == ['make', 'check-progs'])
        self.assertEqual((result, status), (0, '1'))
        self.assertFalse(any('testsuite/tools/check.py' in command for command, _ in calls))

    def test_missing_probe_does_not_stop_tests(self):
        def missing(command):
            if command[0].endswith('/dirfd-probe'):
                raise FileNotFoundError(command[0])
            return False
        result, status, calls = self.run_guest('netbsd', missing)
        self.assertEqual((result, status), (0, '1'))
        self.assertEqual(sum('testsuite/tools/check.py' in command for command, _ in calls), 4)

    def test_host_status(self):
        with tempfile.TemporaryDirectory(prefix='rsync-guest-') as directory:
            path = Path(directory) / 'status'
            for status in ('0', '0\n', '1', '', '00', 'false', '0\n1', ' 0', '0 ', '0\r\n'):
                path.write_text(status)
                with patch.object(sys, 'argv', ['guest.py', 'check', '--output', directory]), redirect_stderr(StringIO()):
                    self.assertEqual(guest['main'](), 0 if status in ('0', '0\n') else 1)
            path.unlink()
            with patch.object(sys, 'argv', ['guest.py', 'check', '--output', directory]), redirect_stderr(StringIO()):
                self.assertEqual(guest['main'](), 1)


if __name__ == '__main__':
    unittest.main()
