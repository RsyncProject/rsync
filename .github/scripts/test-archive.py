import io
from pathlib import Path
import tempfile
import tarfile
import unittest

from archive import GEN_FILES, qualify


class Archive(unittest.TestCase):
    def reject(self, change):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'source.tar.gz'
            entries = {f'rsync-3.5.2/{name}': (0o644, 0, b'generated\n') for name in GEN_FILES}
            for name in ('configure', 'configure.sh', 'rsync-ssl', 'support/rrsync'):
                entries[f'rsync-3.5.2/{name}'] = (0o755, 0, b'executable\n')
            change(entries)
            with tarfile.open(archive, 'w:gz') as output:
                for name, (mode, uid, content) in entries.items():
                    entry = tarfile.TarInfo(name)
                    entry.mode, entry.uid = mode, uid
                    if isinstance(content, tuple):
                        entry.type, entry.linkname = tarfile.SYMTYPE, content[0]
                        output.addfile(entry)
                    else:
                        entry.size = len(content)
                        output.addfile(entry, io.BytesIO(content))
            with self.assertRaises(ValueError):
                qualify(archive, root)
            self.assertFalse((root / 'qualification.json').exists())

    def test_missing_generated(self):
        self.reject(lambda entries: entries.pop('rsync-3.5.2/rsync.1'))

    def test_executable_mode(self):
        self.reject(lambda entries: entries.update({'rsync-3.5.2/configure': (0o644, 0, b'configure')}))

    def test_ownership(self):
        self.reject(lambda entries: entries.update({'rsync-3.5.2/file': (0o644, 1000, b'file')}))

    def test_paths(self):
        for path in ('../outside', 'rsync-3.5.2/../outside', 'rsync-3.5.2/.git/config',
                     'rsync-3.5.2/.github/workflows/ci.yml'):
            with self.subTest(path=path):
                self.reject(lambda entries: entries.update({path: (0o644, 0, b'file')}))

    def test_link_escape(self):
        self.reject(lambda entries: entries.update({'rsync-3.5.2/link': (0o777, 0, ('../../outside',))}))


if __name__ == '__main__':
    unittest.main()
