import io
import json
from pathlib import Path, PurePosixPath
import runpy
import subprocess
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
        run = Mock(return_value=Mock(stdout=json.dumps(dict(filesystems=[dict(target='/fixture', fstype='ext4', options='ro,noexec')])).encode()))
        with patch.dict(storage['mount_options'].__globals__, run=run):
            self.assertEqual(storage['mount_options'](path), ('ext4', {'ro', 'noexec'}))
            run.return_value.stdout = b'{"filesystems": [{"target": "/", "fstype": "ext4", "options": "rw"}]}'
            with self.assertRaises(ValueError):
                storage['mount_options'](path)

    def test_cleanup_closes_a_partially_opened_mapping(self):
        volume = object.__new__(storage['EncryptedVolume'])
        volume.name, volume.mount = 'fixture', Path('/fixture')
        volume.device = Mock()
        volume.device.exists.return_value = False
        run = Mock(side_effect=[Mock(returncode=0), Mock(), Mock(returncode=4)])
        with patch.dict(storage['EncryptedVolume'].close.__globals__, run=run):
            volume.close()
            self.assertIn(unittest.mock.call('cryptsetup', 'close', 'fixture'), run.call_args_list)

    def test_command_does_not_log_key_input(self):
        with patch('sys.stdout', new_callable=io.StringIO) as output, patch('subprocess.run') as child:
            child.return_value = subprocess.CompletedProcess([], 0, b'', b'')
            storage['run']('cryptsetup', 'open', '--key-file=-', 'fixture', input=b'private fixture key')
            self.assertNotIn('private fixture key', output.getvalue())
            self.assertEqual(child.call_args[1]['input'], b'private fixture key')

    def test_cleanup_finds_an_interrupted_loop_allocation(self):
        volume = object.__new__(storage['EncryptedVolume'])
        volume.image = Path('/fixture/image').resolve()
        volume.name, volume.mount, volume.loop = 'fixture', Path('/fixture'), None
        volume.device = Mock()
        volume.device.exists.return_value = False
        device = {'name': '/dev/loop8', 'back-file': str(volume.image)}
        run = Mock(side_effect=[Mock(returncode=4), Mock(stdout=json.dumps(dict(loopdevices=[device])).encode()), Mock(),
                                Mock(stdout=b'{"loopdevices": []}')])
        with patch.dict(storage['EncryptedVolume'].__exit__.__globals__, run=run):
            volume.__exit__(None, None, None)
        self.assertIn(unittest.mock.call('losetup', '--detach', '/dev/loop8'), run.call_args_list)


if __name__ == '__main__':
    unittest.main()
