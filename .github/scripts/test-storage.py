import io
import json
from pathlib import Path, PurePosixPath
import runpy
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

storage = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'testsuite/tools/check-storage.py'))


class Storage(unittest.TestCase):
    def test_refusal_checks_status_and_cause(self):
        for code, message in ((0, b'No space left on device'), (11, b'Permission denied'), (23, b'No space left on device')):
            with self.subTest(code=code, message=message), self.assertRaises(ValueError):
                storage['refused'](subprocess.CompletedProcess([], code, b'', message), 11, 'No space left on device')
        storage['refused'](subprocess.CompletedProcess([], 11, b'', b'No space left on device'), 11, 'No space left on device')

    def test_mount_must_be_the_requested_filesystem(self):
        path = PurePosixPath('/fixture')
        run = Mock(return_value=Mock(returncode=0, stdout=json.dumps(dict(filesystems=[dict(target='/fixture', fstype='ext4', options='ro,noexec')])).encode()))
        with patch.dict(storage['mount_options'].__globals__, run=run):
            self.assertEqual(storage['mount_options'](path), ('ext4', {'ro', 'noexec'}))
            run.return_value.stdout = b'{"filesystems": [{"target": "/", "fstype": "ext4", "options": "rw"}]}'
            with self.assertRaises(ValueError):
                storage['mount_options'](path)
            run.return_value.returncode = 1
            self.assertIsNone(storage['mount_options'](path, required=False))
            run.return_value.returncode = 2
            with self.assertRaises(ValueError):
                storage['mount_options'](path, required=False)

    def test_cleanup_closes_a_partially_opened_mapping(self):
        volume = object.__new__(storage['EncryptedVolume'])
        volume.name, volume.mount = 'fixture', Path('/fixture')
        volume.device = Mock()
        volume.device.exists.return_value = False
        run = Mock(side_effect=[Mock(returncode=0), Mock(), Mock(returncode=4)])
        with patch.dict(storage['EncryptedVolume'].close.__globals__, run=run, mount_options=Mock(return_value=None)):
            volume.close()
            self.assertIn(unittest.mock.call('cryptsetup', 'close', 'fixture'), run.call_args_list)

    def test_command_does_not_log_key_input(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output, patch('subprocess.Popen') as started:
            child = started.return_value.__enter__.return_value
            child.communicate.return_value, child.returncode = (b'', b''), 0
            storage['run']('cryptsetup', 'open', '--key-file=-', 'fixture', input=b'private fixture key')
            self.assertNotIn('private fixture key', output.getvalue())
            child.communicate.assert_called_once_with(b'private fixture key', timeout=90)
            self.assertTrue(started.call_args[1]['start_new_session'])

    def test_cleanup_finds_an_interrupted_loop_allocation(self):
        image = Path('/fixture/image').resolve()
        device = {'name': '/dev/loop8', 'back-file': str(image)}
        run = Mock(side_effect=[Mock(stdout=json.dumps(dict(loopdevices=[device])).encode()), Mock(),
                                Mock(stdout=b'{"loopdevices": []}')])
        with patch.dict(storage['detach_loops'].__globals__, run=run):
            storage['detach_loops'](image)
        self.assertIn(unittest.mock.call('losetup', '--detach', '/dev/loop8'), run.call_args_list)

    def test_loop_identity_and_access_mode(self):
        image, device = Path('/fixture/image').resolve(), PurePosixPath('/dev/loop8')
        record = {'name': str(device), 'back-file': str(image), 'ro': False, 'offset': 0, 'sizelimit': 0}
        run = Mock(return_value=Mock(stdout=json.dumps(dict(loopdevices=[record])).encode()))
        with patch.dict(storage['check_loop'].__globals__, run=run):
            storage['check_loop'](device, image)
            for field, value in (('name', '/dev/sda'), ('back-file', '/other/image'), ('ro', True), ('ro', 0),
                                 ('offset', 512), ('sizelimit', 4096)):
                run.return_value.stdout = json.dumps(dict(loopdevices=[dict(record, **{field: value})])).encode()
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    storage['check_loop'](device, image)
            run.reset_mock()
            with self.assertRaises(ValueError):
                storage['check_loop'](PurePosixPath('/dev/sda'), image)
            run.assert_not_called()

    def test_failed_loop_validation_never_opens_the_device(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'image'
            image.write_bytes(b'fixture')
            detach = Mock()
            with patch.dict(storage['loop_device'].__wrapped__.__globals__, run=Mock(return_value=Mock(stdout=b'/dev/loop8\n')),
                            check_loop=Mock(side_effect=ValueError('foreign image')), detach_loops=detach), \
                    patch('os.open') as opened, self.assertRaises(ValueError):
                with storage['loop_device'](image):
                    self.fail('Unverified device was used')
            opened.assert_not_called()
            detach.assert_called_once_with(image)

    def test_cleanup_does_not_detach_foreign_images(self):
        image = Path('/fixture/image').resolve()
        run = Mock(return_value=Mock(stdout=b'{"loopdevices": [{"name": "/dev/loop8", "back-file": "/other/image"}]}'))
        with patch.dict(storage['detach_loops'].__globals__, run=run), self.assertRaises(ValueError):
            storage['detach_loops'](image)
        self.assertEqual(run.call_count, 1)

    def test_cleanup_waits_for_lazy_loop_release(self):
        image = Path('/fixture/image').resolve()
        attached = json.dumps(dict(loopdevices=[{'name': '/dev/loop8', 'back-file': str(image)}])).encode()
        run = Mock(side_effect=[Mock(stdout=attached), Mock(), Mock(stdout=attached), Mock(stdout=b'{"loopdevices": []}')])
        with patch.dict(storage['detach_loops'].__globals__, run=run), patch('time.monotonic', side_effect=[0, 1]), \
                patch('time.sleep') as sleep:
            storage['detach_loops'](image)
        sleep.assert_called_once_with(0.05)
        self.assertEqual(run.call_args_list.count(unittest.mock.call('losetup', '--detach', '/dev/loop8')), 1)

    def test_loop_release_has_a_deadline(self):
        image = Path('/fixture/image').resolve()
        attached = json.dumps(dict(loopdevices=[{'name': '/dev/loop8', 'back-file': str(image)}])).encode()
        run = Mock(return_value=Mock(stdout=attached))
        with patch.dict(storage['detach_loops'].__globals__, run=run), patch('time.monotonic', side_effect=[0, 6]), \
                self.assertRaises(ValueError):
            storage['detach_loops'](image)

    def test_bind_mount_cleanup_uses_the_mount_table(self):
        path = Path('/fixture')
        run = Mock()
        with patch.dict(storage['mounted'].__wrapped__.__globals__, run=run, mount_options=Mock(return_value=('ext4', {'rw'}))), \
                patch('os.path.ismount', return_value=False):
            with storage['mounted'](path, '--bind', '/source'):
                pass
        self.assertIn(unittest.mock.call('umount', path), run.call_args_list)

    def test_command_timeout_kills_its_process_group(self):
        failure = subprocess.TimeoutExpired(['fixture'], 90)
        with patch('sys.stdout', new_callable=io.StringIO), patch('subprocess.Popen') as started, \
                patch('os.killpg') as kill, self.assertRaises(subprocess.TimeoutExpired):
            child = started.return_value.__enter__.return_value
            child.pid = 1234
            child.communicate.side_effect = [failure, (b'', b'')]
            storage['run']('fixture')
        kill.assert_called_once_with(1234, storage['signal'].SIGKILL)
        self.assertEqual(child.communicate.call_args, unittest.mock.call(timeout=5))

    def test_copy_as_requires_real_supplementary_group_coverage(self):
        root = SimpleNamespace(pw_name='root', pw_uid=0, pw_gid=0)
        account = SimpleNamespace(pw_name='fixture', pw_uid=1000, pw_gid=1000)
        with patch.dict('os.environ', SUDO_USER='root'), patch.object(storage['pwd'], 'getpwnam', return_value=root), \
                patch.object(storage['pwd'], 'getpwall', return_value=[root, account]), \
                patch('os.getgrouplist', return_value=[0, 1000, 27]):
            self.assertEqual(storage['copy_account'](), (account, 27))
        with patch.dict('os.environ', SUDO_USER=''), patch.object(storage['pwd'], 'getpwall', return_value=[account]), \
                patch('os.getgrouplist', return_value=[1000]), self.assertRaises(ValueError):
            storage['copy_account']()

    def test_device_writer_rejects_nonprivate_or_foreign_nodes(self):
        image = Path('/fixture/image')
        device = Mock()
        device.stat.return_value = SimpleNamespace(st_rdev=8)
        run, validate = Mock(), Mock()
        with patch.dict(storage['write_device'].__globals__, run=run, check_loop=validate,
                        device_identity=Mock(return_value=(1, 9))):
            with self.assertRaises(ValueError):
                storage['write_device'](Path('/rsync'), Path('/source'), Path('/dev/loop8'), device, image)
            validate.assert_not_called()
            with self.assertRaises(ValueError):
                storage['write_device'](Path('/rsync'), Path('/source'), Path('/fixture/alias'), device, image)
            run.assert_not_called()

    def test_device_writer_requires_the_same_device_after_copy(self):
        device = Mock()
        device.stat.return_value = SimpleNamespace(st_rdev=8)
        with patch.dict(storage['write_device'].__globals__, run=Mock(), check_loop=Mock(),
                        device_identity=Mock(side_effect=[(1, 8), (2, 8)])), self.assertRaises(ValueError):
            storage['write_device'](Path('/rsync'), Path('/source'), Path('/fixture/alias'), device, Path('/fixture/image'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'regular'
            path.write_bytes(b'data')
            with self.assertRaises(ValueError):
                storage['device_identity'](path)

    def test_copy_as_checks_data_and_both_identity_components(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file'
            path.write_bytes(b'contents')
            info = path.stat()
            storage['copied_as'](path, b'contents', info.st_uid, info.st_gid)
            for contents, uid, gid in ((b'wrong', info.st_uid, info.st_gid),
                                       (b'contents', info.st_uid + 1, info.st_gid),
                                       (b'contents', info.st_uid, info.st_gid + 1)):
                with self.subTest(contents=contents, uid=uid, gid=gid), self.assertRaises(ValueError):
                    storage['copied_as'](path, contents, uid, gid)

    def test_cli_runs_every_storage_group_before_publishing(self):
        groups = ('encrypted', 'filesystem_boundaries', 'device_transfers', 'copy_as', 'restricted', 'full_filesystem')
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            binary = base / 'rsync'
            binary.write_bytes(b'candidate')
            def checked(name):
                return lambda binary, root, checks: checks.append(name)
            for fail in (False, True):
                output = base / str(fail)
                callbacks = {name: checked(name) for name in groups}
                if fail:
                    callbacks['copy_as'] = Mock(side_effect=ValueError('identity check failed'))
                args = ['check-storage.py', '--binary', str(binary), '--output', str(output), '--parent-namespace=old']
                readlink = storage['os'].readlink
                with patch.object(sys, 'argv', args), patch('os.geteuid', return_value=0), \
                        patch('os.readlink', side_effect=lambda path, *args, **kwargs: 'new' if path == '/proc/self/ns/mnt' else readlink(path, *args, **kwargs)), \
                        patch('signal.signal'), \
                        patch.dict(storage['main'].__globals__, callbacks), patch('sys.stdout', new_callable=io.StringIO):
                    if fail:
                        with self.assertRaises(ValueError):
                            storage['main']()
                        self.assertFalse((output / 'receipt.json').exists())
                    else:
                        self.assertEqual(storage['main'](), 0)
                        receipt = json.loads((output / 'receipt.json').read_text())
                        self.assertEqual(receipt['checks'], list(groups))
                        self.assertIs(receipt['cleaned'], True)


if __name__ == '__main__':
    unittest.main()
