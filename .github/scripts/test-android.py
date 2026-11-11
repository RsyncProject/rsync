from contextlib import redirect_stdout
import hashlib
from io import StringIO
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


android = runpy.run_path(str(Path(__file__).with_name('android-emulator.py')))


class Android(unittest.TestCase):
    def device(self, changes=None):
        values = {'getprop ro.build.version.sdk': '36', 'getprop ro.product.cpu.abi': 'x86_64',
                  'getprop ro.kernel.qemu': '1', 'getenforce': 'Enforcing', 'id -u': '2000',
                  'uname -r': '6.6.30-android15', 'getprop ro.build.fingerprint': 'test-device'}
        values.update(changes or {})
        return lambda *args, **kwargs: SimpleNamespace(stdout=values[args[1]] + '\n')

    def test_device(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(android['check_device'](self.device())['uid'], '2000')
            for command, value in {'getprop ro.build.version.sdk': '35',
                                   'getprop ro.product.cpu.abi': 'aarch64',
                                   'getprop ro.kernel.qemu': '', 'getenforce': 'Permissive',
                                   'id -u': '0'}.items():
                with self.subTest(command=command), self.assertRaises(ValueError):
                    android['check_device'](self.device({command: value}))

    def test_boot(self):
        process = SimpleNamespace(poll=lambda: None)
        with patch('time.sleep'), patch('time.monotonic', side_effect=[0, 1]):
            android['wait_for_boot'](lambda *a, **kw: SimpleNamespace(stdout='1\n'), process)
        with self.assertRaises(RuntimeError):
            android['wait_for_boot'](self.device(), SimpleNamespace(poll=lambda: 1))
        for failure in (SimpleNamespace(stdout=''), subprocess.CalledProcessError(1, 'adb'),
                        subprocess.TimeoutExpired('adb', 10)):
            with patch('time.sleep'), patch('time.monotonic', side_effect=[0, 1, 301]), \
                 self.assertRaises(TimeoutError):
                def adb(*args, **kwargs):
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                android['wait_for_boot'](adb, process)

    def test_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            for code, stderr in ((0, ''), (23, 'other error'), (12, 'Permission denied'),
                                 (23, 'Permission denied')):
                calls = []
                def adb(*args, **kwargs):
                    calls.append(args)
                    return SimpleNamespace(returncode=code, stdout='', stderr=stderr)
                with self.subTest(code=code, stderr=stderr):
                    args = (adb, Path('rsync'), '/data/local/tmp/rsync-ci.12345678', Path(directory))
                    if code == 23 and 'Permission denied' in stderr:
                        android['check_permissions'](*args)
                        self.assertIn(('shell', 'test ! -r /data/system'), calls)
                        self.assertTrue(any('test' in call[-1] and '/refused/private' in call[-1]
                                            for call in calls))
                    else:
                        with self.assertRaises(ValueError):
                            android['check_permissions'](*args)

    def test_stop(self):
        adb = Mock()
        process = Mock()
        process.poll.return_value = 0
        android['stop_emulator'](adb, process)
        adb.assert_not_called()
        process.poll.return_value = None
        android['stop_emulator'](adb, process)
        adb.assert_called_once_with('emu', 'kill', timeout=10, capture_output=True)
        process.terminate.assert_not_called()
        process.reset_mock()
        adb.side_effect = subprocess.CalledProcessError(1, 'adb')
        process.wait.side_effect = [subprocess.TimeoutExpired('emulator', 10), 0]
        android['stop_emulator'](adb, process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    def test_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'candidate rsync'
            binary.write_bytes(b'candidate')
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            for mismatch, failed in ((False, False), (True, False), (False, True)):
                (root / 'receipt.json').unlink(missing_ok=True)
                calls = []
                def adb(*args, **kwargs):
                    calls.append(args)
                    command = args[-1]
                    if command.startswith('mktemp'):
                        return SimpleNamespace(stdout='/data/local/tmp/rsync-ci.12345678\n')
                    if command.startswith('sha256sum'):
                        return SimpleNamespace(stdout=f'{"0" * 64 if mismatch else digest}  rsync\n')
                    if failed and 'env ' in command:
                        raise subprocess.CalledProcessError(1, args)
                    return SimpleNamespace(stdout='')
                with patch.dict(android['check_transfer'].__globals__,
                                check_device=lambda adb: {}, check_permissions=lambda *a: None), \
                     patch('subprocess.run', return_value=SimpleNamespace(stdout='revision\n')):
                    if mismatch or failed:
                        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                            android['check_transfer'](adb, binary, root)
                        self.assertFalse((root / 'receipt.json').exists())
                    else:
                        android['check_transfer'](adb, binary, root)
                        self.assertTrue((root / 'receipt.json').is_file())
                self.assertEqual(calls[-1], ('shell', 'rm -rf /data/local/tmp/rsync-ci.12345678'))

    def test_scratch(self):
        for work in ('', '/', '/data/local/tmp/rsync-ci.x/../../escape.12345678',
                     '/data/local/tmp/rsync-ci.12345678;echo bad'):
            calls = []
            def adb(*args, **kwargs):
                calls.append(args)
                return SimpleNamespace(stdout=work)
            with patch.dict(android['check_transfer'].__globals__, check_device=lambda adb: {}), \
                 self.subTest(work=work), self.assertRaises(ValueError):
                android['check_transfer'](adb, Path('rsync'), Path('.'))
            self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
