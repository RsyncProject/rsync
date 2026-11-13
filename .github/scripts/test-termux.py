from contextlib import redirect_stderr
import base64
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

termux = runpy.run_path(str(Path(__file__).with_name('termux.py')))
Termux = termux['Termux']
ROOT = termux['HOME'] + '/rsync-ci.12345678'


class Sandbox(unittest.TestCase):
    def test_identity(self):
        context = 'u:r:untrusted_app_27:s0:c216,c256,c512,c768'
        termux['check_identity']('10216', context, 10216)
        for uid, domain, expected in (('0', context, 10216), ('2000', context, 10216),
                                       ('10217', context, 10216), ('10216', context, 2000),
                                       ('10216', 'u:r:runas_app:s0:c216,c256,c512,c768', 10216),
                                       ('10216', 'u:r:shell:s0', 10216),
                                       ('10216', 'u:r:untrusted_app_27:s0', 10216),
                                       ('10216', 'u:r:su:s0', 10216)):
            with self.subTest(uid=uid, domain=domain, expected=expected), self.assertRaises(ValueError):
                termux['check_identity'](uid, domain, expected)

    def test_download(self):
        payload = b'APK fixture'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'termux.apk'
            with patch('urllib.request.urlopen', return_value=BytesIO(payload)), self.assertRaises(ValueError):
                termux['download_apk'](path)
            path.unlink()
            with patch('urllib.request.urlopen', return_value=BytesIO(payload)), \
                    patch.dict(termux['download_apk'].__globals__, APK_SHA256=hashlib.sha256(payload).hexdigest()):
                termux['download_apk'](path)
            self.assertEqual(path.read_bytes(), payload)
            with self.assertRaises(FileExistsError), patch('urllib.request.urlopen', return_value=BytesIO(payload)):
                termux['download_apk'](path)

    def test_paths(self):
        for root in ('', '/', '/data/local/tmp/rsync-ci.12345678', ROOT + '/..', ROOT + ';echo bad'):
            with self.subTest(root=root), self.assertRaises(ValueError):
                Termux(Mock(), root)
        session = Termux(Mock(), ROOT)
        for path in ('/data/system/secret', ROOT + '/../escape', ROOT + '\0'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                session.write_file(path, b'data')
        session.adb.assert_not_called()

    def test_private_bytes(self):
        payload = b'raw\r\n\0data\x1a' + bytes(range(256))
        adb = Mock(return_value=SimpleNamespace(stdout=base64.b64encode(payload)))
        session = Termux(adb, ROOT)
        session.write_file(ROOT + '/two words', payload)
        self.assertEqual(base64.b64decode(adb.call_args[1]['input']), payload)
        self.assertFalse(adb.call_args[1]['text'])
        self.assertIn("'" + ROOT + "/two words'", adb.call_args[0][-1])
        self.assertEqual(session.read_file(ROOT + '/two words'), payload)
        self.assertIn('head -c 1048577', adb.call_args[0][-1])
        self.assertIn('set -o pipefail', adb.call_args[0][-1])
        adb.return_value = SimpleNamespace(stdout=base64.b64encode(b'x' * 1048577))
        with self.assertRaises(ValueError):
            session.read_file(ROOT + '/large')
        adb.return_value = SimpleNamespace(stdout=b'not base64')
        with self.assertRaises(ValueError):
            session.read_file(ROOT + '/malformed')
        adb.return_value = subprocess.CompletedProcess([], 1, b'')
        self.assertEqual(session.read_file(ROOT + '/missing', optional=True), b'')
        adb.return_value = subprocess.CompletedProcess([], 2, b'')
        with self.assertRaises(subprocess.CalledProcessError):
            session.read_file(ROOT + '/error', optional=True)

    def command(self, *, error=b'-1', code=b'0', stdout=b'result', stderr=b''):
        adb = Mock(return_value=SimpleNamespace(returncode=0, stdout='service started'))
        session = Termux(adb, ROOT)
        values = {'err': error, 'exit_code': code, 'stdout': stdout, 'stderr': stderr}
        def read(path, optional=False):
            return values[path.rsplit('/', 1)[-1]]
        return session, adb, read

    def test_command_uses_the_service(self):
        session, adb, read = self.command(stdout=b'10216\n')
        with patch.object(Termux, 'read_file', side_effect=read), patch.object(Termux, 'write_file') as write:
            result = session.command('/system/bin/id', '-u', capture_output=True)
        self.assertEqual(result.stdout, '10216\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn(b'exec /system/bin/id -u\n', write.call_args[0][1])
        calls = [call[0][-1] for call in adb.call_args_list]
        self.assertTrue(any('com.termux/.app.RunCommandService' in call and 'com.termux.RUN_COMMAND_RESULT_DIRECTORY' in call for call in calls))
        self.assertTrue(any('/result-1/err' in call for call in calls))
        self.assertFalse(any('run-as com.termux /system/bin/id' in call for call in calls))

    def test_failed_and_malformed_results(self):
        for error, code in ((b'0', b'0'), (b'-1', b''), (b'-1', b'not an exit status')):
            session, adb, read = self.command(error=error, code=code)
            with self.subTest(error=error, code=code), patch.object(Termux, 'read_file', side_effect=read), \
                    patch.object(Termux, 'write_file'), self.assertRaises(ValueError):
                session.command('test', '!', '-e', ROOT + '/missing', capture_output=True)
        session, adb, read = self.command(code=b'23', stderr=b'Permission denied')
        with patch.object(Termux, 'read_file', side_effect=read), patch.object(Termux, 'write_file'):
            with self.assertRaises(subprocess.CalledProcessError):
                session.command('rsync', capture_output=True)
            result = session.command('rsync', capture_output=True, check=False)
            self.assertEqual(result.returncode, 23)
            self.assertEqual(result.stderr, 'Permission denied')

    def test_timeout(self):
        session = Termux(Mock(return_value=SimpleNamespace(returncode=1, stdout='')), ROOT)
        with patch.object(Termux, 'write_file'), patch('time.monotonic', side_effect=[0, 31]), \
                self.assertRaises(TimeoutError):
            session.command('sleep', '300', capture_output=True)

    def test_bootstrap(self):
        for message, expected in (('Bootstrap packages installed successfully.', None),
                                  ('Bootstrap Error: failed', ValueError), ('extracting packages', TimeoutError)):
            def adb(*args, **kwargs):
                if args[0] == 'shell':
                    return SimpleNamespace(stdout='2221\n')
                self.assertIn('--pid=2221', args)
                return SimpleNamespace(stdout=message)
            with self.subTest(message=message), patch('time.sleep'), \
                    patch('time.monotonic', side_effect=[0, 1, 181]):
                if expected:
                    with self.assertRaises(expected):
                        termux['wait_for_bootstrap'](adb)
                else:
                    termux['wait_for_bootstrap'](adb)

    def test_refuses_an_existing_installation(self):
        adb = Mock(return_value=SimpleNamespace(stdout='package:com.termux\n'))
        with patch.dict(termux['check_transfer'].__globals__, download_apk=Mock(side_effect=AssertionError('Downloaded over an existing install'))), \
                self.assertRaises(ValueError):
            termux['check_transfer'](adb, Path('rsync'), Path('.'), {})
        self.assertEqual(adb.call_count, 1)

    def test_cleanup_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'rsync'
            binary.write_bytes(b'candidate')
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            for failure in ('install', 'identity', 'transfer', 'cleanup', 'remaining', 'running', 'process-check', None):
                output = root / str(failure)
                output.mkdir()
                calls = []
                primary = subprocess.CalledProcessError(1, 'adb install')
                def adb(*args, **kwargs):
                    calls.append(args)
                    if args[0] == 'install' and failure == 'install':
                        raise primary
                    stdout = ''
                    if args[-1].startswith('pm list packages -U'):
                        stdout = 'package:com.termux uid:10216\n'
                    if 'mktemp -d' in args[-1]:
                        stdout = ROOT + '\n'
                    if any(call[0] == 'uninstall' for call in calls):
                        if args[-1] == 'pm list packages com.termux' and failure == 'remaining':
                            stdout = 'package:com.termux\n'
                        if args[-1] == 'pidof -s com.termux' and failure == 'running':
                            stdout = '2221\n'
                    code = 1 if args[0] == 'uninstall' and failure in ('install', 'cleanup') else 0
                    if args[-1] == 'pidof -s com.termux':
                        code = 2 if failure == 'process-check' else (0 if stdout else 1)
                    return SimpleNamespace(returncode=code, stdout=stdout, stderr='')
                def command(*args, **kwargs):
                    stdout, code, stderr = '', 0, ''
                    if args[:2] == ('/system/bin/id', '-u'):
                        stdout = '2000' if failure == 'identity' else '10216'
                    elif args[:2] == ('/system/bin/id', '-Z'):
                        stdout = 'u:r:untrusted_app_27:s0:c216,c256,c512,c768'
                    elif args[0] == 'sha256sum':
                        stdout = digest + '  rsync\n'
                    elif args[0] == ROOT + '/rsync':
                        code, stderr = 23, 'Permission denied'
                    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
                session = SimpleNamespace(command=command, run_as=Mock(), write_file=Mock(), read_file=Mock())
                fixture = Mock(side_effect=ValueError('transfer failed') if failure == 'transfer' else None)
                with self.subTest(failure=failure), patch.dict(termux['check_transfer'].__globals__,
                        download_apk=Mock(), Termux=Mock(return_value=session), transfer=fixture,
                        wait_for_bootstrap=Mock(),
                        run=Mock(return_value=SimpleNamespace(stdout='revision\n'))), redirect_stderr(StringIO()):
                    if failure:
                        with self.assertRaises(subprocess.CalledProcessError if failure == 'install' else ValueError) as caught:
                            termux['check_transfer'](adb, binary, output, {})
                        if failure == 'install':
                            self.assertIs(caught.exception, primary)
                        self.assertFalse((output / 'receipt.json').exists())
                    else:
                        termux['check_transfer'](adb, binary, output, {})
                        self.assertIs(json.loads((output / 'receipt.json').read_text())['app']['cleaned'], True)
                self.assertIn(('shell', 'am', 'force-stop', 'com.termux'), calls)
                self.assertIn(('uninstall', 'com.termux'), calls)


if __name__ == '__main__':
    unittest.main()
