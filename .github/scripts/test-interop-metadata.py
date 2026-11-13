from collections import Counter
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'testsuite'))
from harness import interop_metadata as metadata
from harness.interoperability import PLATFORMS

endpoint = runpy.run_path(str(Path(__file__).with_name('interop-endpoint.py')))


class Attributes(unittest.TestCase):
    def test_command_failures_identify_the_tool(self):
        failures = [FileNotFoundError(2, 'missing'),
                    subprocess.CalledProcessError(1, ['newfs'], stderr=b'cannot format device\n'),
                    subprocess.TimeoutExpired(['newfs'], 60)]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(metadata.subprocess, 'run', side_effect=failure), \
                    self.assertRaisesRegex(ValueError, 'newfs'):
                metadata.command('newfs', '/dev/fixture')
        with patch.object(metadata.subprocess, 'run', side_effect=failures[1]), \
                self.assertRaisesRegex(ValueError, 'cannot format device'):
            metadata.command('newfs', '/dev/fixture')

    def test_freebsd_default_acls_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for phase in (1, 2):
                with self.subTest(phase=phase), patch.object(metadata, 'command') as command:
                    metadata.set_acl(path, 'freebsd', phase)
                    calls = [call[0] for call in command.call_args_list]
                    self.assertEqual(calls[:2], [('setfacl', '-b', path), ('setfacl', '-k', path)])
                    self.assertEqual(calls[2][:2], ('setfacl', '-m'))
                    self.assertEqual(calls[3][:3], ('setfacl', '-d', '-m'))
                    self.assertEqual(calls[2][2], calls[3][3])
                    self.assertNotIn('default:', calls[2][2])
                    self.assertEqual('group:544:' in calls[2][2], phase == 1)
            access = b'user::rwx\ngroup::r-x\nother::---\n'
            default = b'user::rwx\ngroup::---\nother::---\n'
            with patch.object(metadata, 'command', side_effect=[access, default]) as command:
                result = metadata.read_acl(path, 'freebsd')
                self.assertEqual(result['group:'], 'r-x')
                self.assertEqual(result['default:group:'], '---')
                self.assertEqual(command.call_args_list[1][0], ('getfacl', '-n', '-d', path))
            path.joinpath('file').touch()
            with patch.object(metadata, 'command', return_value=access) as command:
                metadata.read_acl(path / 'file', 'freebsd')
                self.assertEqual(command.call_count, 1)

    def test_case_contract(self):
        self.assertEqual(set(metadata.FEATURES), set(PLATFORMS))
        cases = metadata.metadata_cases()
        self.assertEqual(len(cases), len(set(cases)))
        self.assertEqual(Counter(case[-1] for case in cases), {'xattr': 168, 'empty-xattr': 96, 'acl': 136, 'backup': 80})
        expected = {(source, destination, client, transport, 'xattr')
                    for source in PLATFORMS for destination in PLATFORMS if source != destination
                    for client in ('push', 'pull') for transport in ('ssh', 'daemon')}
        self.assertEqual({case for case in cases if case[-1] == 'xattr'}, expected)
        for source, destination, client, transport, kind in cases:
            self.assertNotEqual(source, destination)
            self.assertIn(client, ('push', 'pull'))
            self.assertIn(transport, ('ssh', 'daemon'))
            if kind == 'backup':
                self.assertTrue(metadata.FEATURES[source][0])
                self.assertTrue(metadata.FEATURES[destination][1])
            elif kind == 'acl' and metadata.FEATURES[source][0] and metadata.FEATURES[destination][0]:
                self.assertEqual(metadata.FEATURES[source][0], metadata.FEATURES[destination][0])
        self.assertIn(('macos', 'linux', 'push', 'ssh', 'backup'), cases)
        self.assertIn(('netbsd', 'solaris', 'pull', 'daemon', 'acl'), cases)
        self.assertIn(('openbsd', 'cygwin', 'push', 'ssh', 'xattr'), cases)

    def test_posix_acl_formats(self):
        source = '# file: fixture\nuser::rw-\ngroup::---\ngroup:545:r-- #effective:r--\nmask::r--\nother::---\n'
        value = metadata.parse_acl(source)
        self.assertEqual(value['group:545'], 'r--')
        self.assertEqual(metadata.parse_acl(source.replace('mask::', 'mask:').replace('other::', 'other:')), value)
        with patch.object(metadata.grp, 'getgrnam', return_value=Mock(gr_gid=545)):
            self.assertEqual(metadata.parse_acl(source.replace('group:545:', 'group:users:')), value)
        for text in ('user::rw-\n', 'default\n', source + 'group:545:rw-\n', source + 'everyone@:rwx:allow\n', source.replace('r--', 'read')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                metadata.parse_acl(text)

    def test_missing_binary_feature_is_not_unsupported(self):
        for capabilities in ({'ACLs': False, 'xattrs': True}, {'ACLs': True, 'xattrs': False}):
            with self.subTest(capabilities=capabilities), patch.object(metadata, 'command', return_value=json.dumps(dict(capabilities=capabilities)).encode()), \
                    patch.object(metadata, 'seed_metadata') as seed, self.assertRaises(ValueError):
                metadata.qualify(Path('/rsync'), Path('/fixture'), 'linux')
            seed.assert_not_called()

    def test_fake_super_is_only_applied_to_the_backup_side(self):
        root = Path('/fixture')
        peer = dict(ssh_user='fixture', address='100.64.0.2')
        for transport in ('ssh', 'daemon'):
            command = Mock()
            with self.subTest(transport=transport), patch.dict(endpoint['copy'].__globals__, command=command):
                endpoint['copy'](root, Path('/rsync'), peer, transport, True, Path('/source'), 'metadata/destination/case/',
                                 ['-rltpA'], fake_remote=True)
                args = command.call_args[0][0]
                if transport == 'ssh':
                    self.assertIn('-M--fake-super', args)
                    self.assertNotIn('--fake-super', args)
                else:
                    self.assertTrue(args[-1].startswith('rsync://100.64.0.2:18733/backup/'))
                    self.assertNotIn('-M--fake-super', args)

    def test_disks_are_detached_when_a_mount_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                if args[0] == 'mdconfig' and '-a' in args:
                    return b'md8\n'
                if args[0] == 'mount':
                    raise subprocess.CalledProcessError(1, args)
                return b''
            with patch.object(metadata, 'command', side_effect=command), self.assertRaises(subprocess.CalledProcessError):
                with metadata.metadata_filesystem(root, 'freebsd'):
                    self.fail('Unsuccessful mount was used')
            self.assertIn(('mdconfig', '-d', '-u', '8'), calls)
            self.assertFalse(any(call[0] == 'umount' for call in calls))

    def test_incorrect_acl_and_xattr_values_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'directory').mkdir()
            (root / 'directory/file').write_text('metadata transfer\n')
            with patch.object(metadata, 'read_acl', return_value={'user:': 'rw-', 'group:': '---', 'other:': '---'}), \
                    self.assertRaises(ValueError):
                metadata.metadata_snapshot(root, 'linux', 'acl')
            with patch.object(metadata.xattrs, 'get_value', return_value=b'wrong'), self.assertRaises(ValueError):
                metadata.metadata_snapshot(root, 'linux', 'xattr')
            values = {'rsync-ci': b'second', 'rsync-ci-large': b'B' * 512}
            with patch.object(metadata.xattrs, 'get_value', side_effect=lambda key, _: values[key]), \
                    patch.object(metadata.xattrs, 'dump_values', return_value='user.rsync-ci-remove=stale'), \
                    self.assertRaises(ValueError):
                metadata.metadata_snapshot(root, 'linux', 'xattr', 2)


if __name__ == '__main__':
    unittest.main()
