import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import patch


changes = runpy.run_path(str(Path(__file__).with_name('changes.py')))
select = changes['select']
families = changes['FAMILIES']
full_selection = {*families, 'versions', 'asan', 'coverage', 'clang'}


class Changes(unittest.TestCase):
    def enabled(self, paths):
        return {name for name, value in select(paths).items() if value}

    def test_documentation_selects_no_test_family(self):
        for paths in (['README.md'], ['SECURITY.md', '.github/INTEROPERABILITY.md'],
                      ['INSTALL.md', 'NEWS.md', '.github/FUNDING.yml'],
                      ['README.md', 'SECURITY.md', 'rsync-web/security.html', 'rsync-web/bug-tracking.html'],
                      ['rsync-web/header.html', 'rsync-web/tech_report/tech_report.css', 'rsync-web/bar1.jpg']):
            with self.subTest(paths=paths):
                self.assertEqual(self.enabled(paths), set())

    def test_generated_document_inputs_select_build_qualification(self):
        self.assertEqual(self.enabled(['rsync.1.md']), full_selection)
        for path in ('rsync-ssl.1.md', 'rsyncd.conf.5.md', 'support/rrsync.1.md',
                     'rsync.1', 'rsync.1.html'):
            with self.subTest(path=path):
                self.assertEqual(self.enabled([path]), {'archive', 'diagnostics'})

    def test_editorconfig_selects_no_test_family(self):
        self.assertEqual(self.enabled(['.editorconfig']), set())

    def test_production_paths_select_the_full_matrix(self):
        for path in ('receiver.c', 'Makefile.in', 'packaging/release.py',
                     'README.c', 'support/README.md', 'lib/new.c', 'wildtest.txt',
                     'rsync-web/bin/upload', 'rsync-web/rsync_wrapper.pl'):
            with self.subTest(path=path):
                self.assertEqual(self.enabled([path]), full_selection)

    def test_unclassified_paths_fail_regardless_of_other_changes(self):
        for path in ('new-file', 'new-document.md', 'liberty/file.c',
                     '.github/scripts/new-helper.py', '.github/workflows/new.yml',
                     '.github/actions/new/action.yml', 'rsync-web/new-helper.py',
                     '.github/scripts/nested/archive.py', '.github/workflows/nested/linux.yml'):
            for paths in ([path], ['receiver.c', path], [path, 'receiver.c']):
                with self.subTest(paths=paths), self.assertRaisesRegex(ValueError, 'Unclassified'):
                    select(paths)

    def test_testsuite_avoids_unrelated_product_lanes(self):
        expected = {'linux', 'bsd', 'macos', 'other', 'versions', 'diagnostics', 'asan'}
        self.assertEqual(self.enabled(['testsuite/tests/transfer/exclude.py']), expected)
        self.assertEqual(self.enabled(['testsuite/harness/runner.py']), expected | {'valgrind'})
        self.assertEqual(self.enabled(['testsuite/harness/interop_metadata.py']),
                         {'diagnostics', 'interoperability'})
        self.assertEqual(self.enabled(['testsuite/profiles/valgrind.json']),
                         {'valgrind', 'diagnostics'})
        self.assertEqual(self.enabled(['testsuite/profiles/archive.json']),
                         {'archive', 'diagnostics'})
        self.assertEqual(self.enabled(['testsuite/profiles/asan.json']),
                         {'asan', 'diagnostics'})
        self.assertEqual(self.enabled(['testsuite/tools/check-storage.py']),
                         {'linux', 'diagnostics'})

    def test_ci_files_select_their_owner(self):
        cases = {
            '.github/workflows/linux.yml': {'linux', 'diagnostics'},
            '.github/workflows/ci.yml': full_selection,
            '.github/workflows/diagnostics.yml': {'diagnostics', 'versions', 'asan', 'coverage', 'clang'},
            '.github/workflows/versions.yml': {'diagnostics', 'versions'},
            '.github/scripts/versions.py': {'diagnostics', 'versions'},
            '.github/workflows/interoperability.yml': {'interoperability', 'diagnostics'},
            '.github/scripts/android-emulator.py': {'android', 'diagnostics'},
            '.github/scripts/test-archive.py': {'archive', 'diagnostics'},
            '.github/scripts/guest.py': {'linux', 'bsd', 'other', 'diagnostics'},
            '.github/scripts/diagnostics.py': {'diagnostics', 'valgrind', 'asan', 'coverage', 'clang'},
            '.github/scripts/test-diagnostics.py': {'diagnostics'},
            '.github/scripts/check-results.py': full_selection,
            '.github/scripts/test-results.py': {'diagnostics'},
            '.github/scripts/changes.py': full_selection,
            '.github/scripts/test-changes.py': {'diagnostics'},
            '.github/scripts/check-platform.py': {'linux', 'macos', 'other', 'diagnostics'},
            '.github/actions/interoperability/action.yml': {'interoperability', 'diagnostics'},
            '.github/actions/interop-upload/action.yml': {'interoperability', 'diagnostics'},
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.enabled([path]), expected)

    def test_master_pushes_share_only_their_own_concurrency_group(self):
        workflow = (Path(__file__).resolve().parents[1] / 'workflows/ci.yml').read_text()
        self.assertIn("group: ${{ github.workflow }}-${{ github.event_name == 'push' && 'master' "
                      "|| github.event.pull_request.number || github.run_id }}", workflow)
        self.assertIn('cancel-in-progress: true', workflow)

    def test_staged_families_keep_selection_after_prerequisite_failure(self):
        workflow = (Path(__file__).resolve().parents[1] / 'workflows/ci.yml').read_text()
        for family, prerequisite in (('android', 'macos'), ('archive', 'macos')):
            with self.subTest(family=family):
                match = re.search(r'^  ' + family + r':\n(.*?)(?=^  [a-z][a-z0-9_]*:\n|\Z)',
                                  workflow, re.M | re.S)
                self.assertIsNotNone(match)
                block = match.group(1)
                self.assertIn('needs: [changes, ' + prerequisite + ']', block)
                condition = next(line.strip() for line in block.splitlines() if line.strip().startswith('if: '))
                prefix, suffix = 'if: ${{ !cancelled() && ', ' }}'
                self.assertTrue(condition.startswith(prefix) and condition.endswith(suffix))
                selection = condition[len(prefix):-len(suffix)]
                self.assertIn('needs.changes.outputs.' + family + " == 'true'", selection)
                expected = 'CI_EXPECT_' + family.upper() + ': ${{ ' + selection + ' }}'
                self.assertIn(expected, [line.strip() for line in workflow.splitlines()])

    def test_archived_peers_run_within_diagnostics(self):
        workflows = Path(__file__).resolve().parents[1] / 'workflows'
        ci = (workflows / 'ci.yml').read_text()
        diagnostics = (workflows / 'diagnostics.yml').read_text()
        self.assertNotIn('versions', families)
        self.assertNotIn('./.github/workflows/versions.yml', ci)
        self.assertIn('CI_VERSIONS_SELECTED: *versions_selected', ci)
        self.assertIn("github.event.schedule == '52 8 * * 1'", ci)
        _, job, workflow = diagnostics.partition('\n  versions:\n')
        self.assertTrue(job)
        self.assertIn('needs: [lint, python_37, asan, coverage, clang_21]', workflow)
        self.assertIn('if: ${{ !cancelled() && inputs.versions }}', workflow)
        self.assertEqual(workflow.count('run: make check-progs'), 1)
        self.assertNotIn('matrix.role', workflow)
        self.assertEqual(workflow.count("!cancelled() && steps.build.outcome == 'success'"), 2)
        for role in ('primary', 'secondary'):
            with self.subTest(role=role):
                self.assertIn('versions.py --role ' + role + ' --output test-results/version-mix/' + role,
                              workflow)
                self.assertIn('name: version-mix-' + role + '-receipts', workflow)
                self.assertIn('path: test-results/version-mix/' + role, workflow)

    def test_valgrind_stays_parallel_for_fast_feedback(self):
        ci = (Path(__file__).resolve().parents[1] / 'workflows/ci.yml').read_text()
        valgrind = (Path(__file__).resolve().parents[1] / 'workflows/valgrind.yml').read_text()
        self.assertIn('  valgrind:\n    name: Valgrind\n    needs: changes\n', ci)
        self.assertIn('CI_EXPECT_VALGRIND: *valgrind_selected', ci)
        self.assertEqual(valgrind.count('            privilege:'), 4)

    def test_multiple_paths_union_their_owners(self):
        self.assertEqual(self.enabled(['README.md', '.github/scripts/android.py',
                                       'testsuite/tests/transfer/exclude.py']),
                         {'linux', 'bsd', 'macos', 'other', 'versions',
                          'android', 'diagnostics', 'asan'})

    def test_no_changed_paths_selects_no_test_family(self):
        self.assertEqual(self.enabled([]), set())

    def test_invalid_paths_are_rejected(self):
        for paths in ([''], ['../receiver.c'], ['/receiver.c']):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                select(paths)

    def test_pull_request_revisions_and_git_result_are_validated(self):
        event = {'pull_request': {'base': {'sha': 'a' * 40}, 'head': {'sha': 'b' * 40}}}
        result = subprocess.CompletedProcess([], 0, b'README.md\0receiver.c\0', b'')
        with patch.object(changes['subprocess'], 'run', return_value=result) as run:
            self.assertEqual(changes['changed_paths'](event, 'pull_request'), ['README.md', 'receiver.c'])
        self.assertEqual(run.call_args[0][0][-1], 'a' * 40 + '...' + 'b' * 40)
        self.assertIn('--no-renames', run.call_args[0][0])
        self.assertIn('--diff-filter=ACDMRTUXB', run.call_args[0][0])
        for mutation in ({}, {'pull_request': {}},
                         {'pull_request': {'base': {'sha': 'x'}, 'head': {'sha': 'b' * 40}}}):
            with self.subTest(event=mutation), self.assertRaises(ValueError):
                changes['changed_paths'](mutation, 'pull_request')
        with patch.object(changes['subprocess'], 'run', return_value=subprocess.CompletedProcess([], 1, b'', b'')), \
                self.assertRaises(ValueError):
            changes['changed_paths'](event, 'pull_request')

    def test_push_uses_the_whole_before_after_range(self):
        event = {'before': 'a' * 40, 'after': 'b' * 40}
        result = subprocess.CompletedProcess([], 0, b'receiver.c\0README.md\0', b'')
        with patch.object(changes['subprocess'], 'run', return_value=result) as run:
            self.assertEqual(changes['changed_paths'](event, 'push'), ['receiver.c', 'README.md'])
        self.assertEqual(run.call_args[0][0][-1], 'a' * 40 + '..' + 'b' * 40)
        for mutation in ({}, {'before': 'a' * 40}, {'before': 'x', 'after': 'b' * 40},
                         {'before': 'a' * 40, 'after': '0' * 40}, None, []):
            with self.subTest(event=mutation), self.assertRaises(ValueError):
                changes['changed_paths'](mutation, 'push')

    def test_first_push_classifies_the_whole_tree(self):
        event = {'before': '0' * 40, 'after': 'b' * 40}
        result = subprocess.CompletedProcess([], 0, b'README.md\0receiver.c\0', b'')
        with patch.object(changes['subprocess'], 'run', return_value=result) as run:
            paths = changes['changed_paths'](event, 'push')
        self.assertEqual(run.call_args[0][0], ['git', 'ls-tree', '--name-only', '-r', '-z', 'b' * 40])
        self.assertEqual(self.enabled(paths), full_selection)

    def test_unclassified_push_does_not_write_partial_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'before': 'a' * 40, 'after': 'b' * 40}))
            git = subprocess.CompletedProcess([], 0, b'receiver.c\0unclassified\0', b'')
            with patch.dict(os.environ, GITHUB_EVENT_NAME='push', GITHUB_EVENT_PATH=str(event),
                            GITHUB_OUTPUT=str(output)), patch.object(changes['subprocess'], 'run', return_value=git), \
                    self.assertRaisesRegex(ValueError, 'unclassified'):
                changes['main']()
            self.assertFalse(output.exists())

    def test_main_writes_every_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'pull_request': {'base': {'sha': 'a' * 40},
                                                              'head': {'sha': 'b' * 40}}}))
            git = subprocess.CompletedProcess([], 0, b'README.md\0', b'')
            with patch.dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(event),
                            GITHUB_OUTPUT=str(output)), patch.object(changes['subprocess'], 'run', return_value=git):
                self.assertEqual(changes['main'](), 0)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(set(values), set(changes['OUTPUTS']))
            self.assertEqual(set(values.values()), {'false'})

    def test_push_of_the_security_documentation_selects_no_test_family(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            event = Path(directory) / 'event.json'
            event.write_text(json.dumps({'before': 'a' * 40, 'after': 'b' * 40}))
            git = subprocess.CompletedProcess([], 0,
                b'README.md\0SECURITY.md\0rsync-web/security.html\0rsync-web/bug-tracking.html\0', b'')
            with patch.dict(os.environ, GITHUB_EVENT_NAME='push', GITHUB_EVENT_PATH=str(event),
                            GITHUB_OUTPUT=str(output)), patch.object(changes['subprocess'], 'run', return_value=git):
                self.assertEqual(changes['main'](), 0)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(set(values), set(changes['OUTPUTS']))
            self.assertEqual(set(values.values()), {'false'})

    def test_scheduled_and_manual_runs_select_every_family(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            for event in ('schedule', 'workflow_dispatch'):
                with self.subTest(event=event), \
                        patch.dict(os.environ, GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(output)):
                    self.assertEqual(changes['main'](), 0)
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
                self.assertEqual(set(values.values()), {'true'})

    def test_unknown_event_does_not_write_a_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output'
            with patch.dict(os.environ, GITHUB_EVENT_NAME='unknown', GITHUB_OUTPUT=str(output)), \
                    self.assertRaises(ValueError):
                changes['main']()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
