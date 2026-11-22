import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import interop_upload as upload
from interop_network import NetworkError


class UploadRecovery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'transfer2.json'
        self.raw = (json.dumps(dict(run_id='123', run_attempt='2', source_sha='a' * 40,
                                    role='linux', passed=True)) + '\n').encode()
        self.path.write_bytes(self.raw)
        self.name = 'interoperability-transfer2-linux-attempt-2'
        environment = patch.dict(os.environ, GITHUB_REPOSITORY_ID='266877886',
                                 GITHUB_RUN_ID='123', GH_TOKEN='fixture')
        environment.start()
        self.addCleanup(environment.stop)

    def test_matching_published_record_needs_no_second_upload(self):
        listing = [dict(id=1, name=self.name, expired=False)]
        with patch.object(upload, 'list_artifacts', return_value=(listing, None)), \
                patch.object(upload, 'download_records', return_value={None: ({}, self.raw)}) as download:
            self.assertTrue(upload.check_published_record(self.name, self.path, timeout=1))
        download.assert_called_once_with('123', {self.name: None}, timeout=1, initial_listing=listing)

    def test_only_missing_record_permits_a_second_upload(self):
        with patch.object(upload, 'list_artifacts', return_value=([], None)), \
                patch.object(upload, 'download_records') as download:
            self.assertFalse(upload.check_published_record(self.name, self.path, timeout=1))
        download.assert_not_called()
        for error in (NetworkError('Read denied', 403), NetworkError('Connection failed', retryable=True),
                      NetworkError('Invalid archive')):
            with self.subTest(error=error), patch.object(upload, 'list_artifacts', side_effect=error), \
                    self.assertRaises(NetworkError):
                upload.check_published_record(self.name, self.path, timeout=1)
        listing = [dict(id=1, name=self.name, expired=False)]
        with patch.object(upload, 'list_artifacts', return_value=(listing, None)), \
                patch.object(upload, 'download_records', side_effect=NetworkError('Archive unavailable')), \
                self.assertRaisesRegex(NetworkError, 'Archive unavailable'):
            upload.check_published_record(self.name, self.path, timeout=1)
        with patch.object(upload, 'list_artifacts') as listing, \
                patch.object(upload, 'download_records', return_value={None: ({}, self.raw)}) as download:
            self.assertTrue(upload.check_published_record(self.name, self.path, timeout=1, allow_missing=False))
        listing.assert_not_called()
        download.assert_called_once_with('123', {self.name: None}, timeout=1, initial_listing=None)

    def test_conflicting_or_invalid_records_fail_closed(self):
        listing = [dict(id=1, name=self.name, expired=False)]
        with patch.object(upload, 'list_artifacts', return_value=(listing, None)), \
                patch.object(upload, 'download_records', return_value={None: ({}, b'other')}), \
                self.assertRaisesRegex(NetworkError, 'differs'):
            upload.check_published_record(self.name, self.path, timeout=1)
        with patch.object(upload, 'list_artifacts', return_value=(listing + [dict(listing[0], id=2)], None)), \
                self.assertRaisesRegex(NetworkError, 'Duplicate'):
            upload.check_published_record(self.name, self.path, timeout=1)
        for name in ('other', 'interoperability-transfer2-linux-attempt-0'):
            with self.subTest(name=name), self.assertRaises(NetworkError):
                upload.check_published_record(name, self.path, timeout=1)
        with patch.dict(os.environ, GITHUB_REPOSITORY_ID='1'), self.assertRaises(NetworkError):
            upload.check_published_record(self.name, self.path, timeout=1)
        self.path.write_bytes(b'not JSON')
        with self.assertRaises(NetworkError):
            upload.check_published_record(self.name, self.path, timeout=1)

    def test_local_record_is_bounded_before_reading(self):
        self.path.write_bytes(b'x' * 65537)
        with patch.object(upload, 'list_artifacts') as listing, \
                self.assertRaisesRegex(NetworkError, 'size limit'):
            upload.check_published_record(self.name, self.path, timeout=1)
        listing.assert_not_called()
        self.path.write_bytes(b'[]')
        with self.assertRaisesRegex(NetworkError, 'Invalid local'):
            upload.check_published_record(self.name, self.path, timeout=1)

    def test_probe_and_require_keep_missing_records_distinct(self):
        output = Path(self.directory.name) / 'github-output'
        with patch.dict(os.environ, GITHUB_OUTPUT=str(output)), \
                patch.object(upload, 'check_published_record', return_value=False) as check, \
                patch.object(sys, 'argv', ['interop_upload.py', 'probe', self.name, str(self.path)]):
            self.assertEqual(upload.main(), 0)
        check.assert_called_once_with(self.name, self.path, timeout=10, allow_missing=True)
        self.assertEqual(output.read_text(), 'present=false\n')
        with patch.object(upload, 'check_published_record', return_value=False) as check, \
                patch.object(sys, 'argv', ['interop_upload.py', 'require', self.name, str(self.path)]), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(upload.main(), 1)
        check.assert_called_once_with(self.name, self.path, timeout=30, allow_missing=False)
        self.assertIn('Rendezvous artefact is missing', error.getvalue())

    def test_all_interoperability_uploads_use_verified_recovery(self):
        root = Path(__file__).resolve().parents[1]
        recovery = (root / 'actions/interop-upload/action.yml').read_text()
        self.assertEqual(recovery.count('continue-on-error: true'), 2)
        self.assertEqual(recovery.count("steps.probe.outputs.present == 'false'"), 2)
        self.assertIn("steps.upload.outcome == 'failure'", recovery)
        self.assertNotIn('overwrite:', recovery)
        for name, count in (('actions/interoperability/action.yml', 10),
                            ('workflows/interoperability.yml', 7)):
            with self.subTest(name=name):
                text = (root / name).read_text()
                self.assertEqual(text.count('uses: ./.github/actions/interop-upload'), count)
                self.assertNotIn('uses: actions/upload-artifact@', text)


if __name__ == '__main__':
    unittest.main()
