import copy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest


checker = Path(__file__).with_name('check-results.py')
check = runpy.run_path(str(checker))['check']


class Results(unittest.TestCase):
    def setUp(self):
        self.expected = dict(linux='true', diagnostics='true', bsd='false', macos='true',
                             other='true', android='true', archive='true', valgrind='true', interoperability='true')
        self.selections = dict(self.expected, versions='true', asan='false', coverage='false',
                               static_analysis='false')
        self.versions_selected = 'true'
        interop = {name: {'result': 'success', 'outputs': {'blocked': 'false'}}
                   for name in ('linux', 'freebsd', 'netbsd', 'openbsd', 'macos', 'solaris', 'cygwin')}
        interop['result'] = {'result': 'success'}
        self.data = {
            'changes': {'result': 'success', 'outputs': {}},
            'interoperability': {'result': 'success', 'outputs': {'jobs': json.dumps(interop)}},
            'archive': {'result': 'success', 'outputs': {'jobs': json.dumps({'archive': {'result': 'success'}})}},
            'linux': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'ubuntu': {'result': 'success'}, 'almalinux': {'result': 'skipped'}}),
                'inputs': json.dumps({'ubuntu': True, 'almalinux': False})}},
            'diagnostics': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'static_analysis': {'result': 'success'},
                                    'asan': {'result': 'skipped'}, 'python_37': {'result': 'success'},
                                    'versions': {'result': 'success'}}),
                'inputs': json.dumps({'static_analysis': True, 'asan': False, 'python_37': True, 'versions': True})}},
            'bsd': {'result': 'skipped', 'outputs': {}},
            'macos': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'macos': {'result': 'success'}})}},
            'other': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'cygwin': {'result': 'success'}, 'solaris': {'result': 'skipped'}}),
                'inputs': json.dumps({'cygwin': True, 'solaris': False})}},
            'android': {'result': 'success', 'outputs': {
                'jobs': json.dumps({name: {'result': 'success'} for name in ('bionic', 'x86_64', 'termux', 'emulator')}),
                'inputs': json.dumps(dict(bionic=True, x86_64=True, termux=True, emulator=True))}},
            'valgrind': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'test': {'result': 'success'}})}},
        }

    def verify(self, scheduled=False):
        return check(self.data, self.expected, self.selections,
                     versions_selected=self.versions_selected, scheduled=scheduled)

    def reject(self):
        with self.assertRaises((TypeError, ValueError)):
            self.verify()

    def test_success(self):
        self.assertTrue(self.verify())

    def test_families(self):
        original = copy.deepcopy(self.data)
        for name in original:
            with self.subTest(missing=name):
                self.data = {key: value for key, value in original.items() if key != name}
                self.reject()
        self.data = {**original, 'extra': {'result': 'success', 'outputs': {}}}
        self.reject()
        self.data = {}
        self.reject()

    def test_family_results(self):
        for name in self.data:
            expected = self.data[name]['result']
            for result in ('success', 'failure', 'cancelled', 'skipped', None):
                if result == expected:
                    continue
                with self.subTest(family=name, result=result):
                    self.data[name]['result'] = result
                    self.reject()
            self.data[name]['result'] = expected

    def test_selections(self):
        for name in self.expected:
            with self.subTest(disabled=name):
                original = self.data[name]
                self.expected[name] = 'false'
                self.selections[name] = 'false'
                if name == 'diagnostics':
                    self.selections['versions'] = self.versions_selected = 'false'
                self.data[name] = {'result': 'skipped', 'outputs': {}}
                self.assertTrue(self.verify())
                self.data[name] = {'result': 'success', 'outputs': {}}
                self.reject()
                self.data[name] = original
                self.expected[name] = 'false' if name == 'bsd' else 'true'
                self.selections[name] = self.expected[name]
                if name == 'diagnostics':
                    self.selections['versions'] = self.versions_selected = 'true'
        self.expected = {name: 'false' for name in self.expected}
        self.selections = {**self.selections, **self.expected, 'versions': 'false'}
        self.versions_selected = 'false'
        self.data = {name: ({'result': 'success', 'outputs': {}} if name == 'changes'
                            else {'result': 'skipped', 'outputs': {}}) for name in self.data}
        self.assertTrue(self.verify())
        for value in ('TRUE', '', True, 1):
            self.expected['linux'] = value
            self.reject()

    def test_nested_results(self):
        outputs = self.data['linux']['outputs']
        original = copy.deepcopy(outputs)
        for value in ('{}', '[]', 'null', 'not JSON', None):
            with self.subTest(jobs=value):
                outputs['jobs'] = value
                self.reject()
        outputs.clear()
        self.reject()
        outputs.update(original)
        for field in ('inputs', 'jobs'):
            for value in ('[]', 'not JSON', 'null'):
                with self.subTest(field=field, value=value):
                    outputs[field] = value
                    self.reject()
            outputs.update(original)
        jobs = json.loads(original['jobs'])
        for value in ('failure', 'cancelled', 'skipped', None):
            outputs['jobs'] = json.dumps({**jobs, 'ubuntu': {'result': value}})
            self.reject()
        outputs['jobs'] = json.dumps({'almalinux': jobs['almalinux']})
        self.reject()
        outputs['jobs'] = json.dumps({**jobs, 'almalinux': {'result': 'success'}})
        self.reject()
        outputs.update(original)
        for inputs in ({'unknown': True}, {'ubuntu': 'true'}, {'ubuntu': 1}, {'ubuntu': False},
                       {'ubuntu': False, 'almalinux': False}):
            outputs['inputs'] = json.dumps(inputs)
            self.reject()

    def test_diagnostics_jobs(self):
        outputs = self.data['diagnostics']['outputs']
        jobs = json.loads(outputs['jobs'])
        for result in ('failure', 'cancelled', 'skipped', None):
            with self.subTest(result=result):
                outputs['jobs'] = json.dumps({**jobs, 'static_analysis': {'result': result}})
                self.reject()
        outputs['jobs'] = json.dumps({key: value for key, value in jobs.items() if key != 'static_analysis'})
        self.reject()
        outputs['jobs'] = json.dumps({**jobs, 'static_analysis': {'result': 'skipped'}})
        outputs['inputs'] = json.dumps({'static_analysis': False, 'asan': False, 'python_37': True, 'versions': True})
        self.assertTrue(self.verify())

    def test_selected_archived_peers_cannot_be_omitted(self):
        outputs = self.data['diagnostics']['outputs']
        jobs, inputs = json.loads(outputs['jobs']), json.loads(outputs['inputs'])
        for status in ('failure', 'cancelled', 'skipped'):
            with self.subTest(status=status):
                outputs['jobs'] = json.dumps({**jobs, 'versions': {'result': status}})
                self.reject()
        outputs['jobs'] = json.dumps({name: value for name, value in jobs.items() if name != 'versions'})
        outputs['inputs'] = json.dumps({name: value for name, value in inputs.items() if name != 'versions'})
        self.reject()
        outputs['jobs'] = json.dumps({**jobs, 'versions': {'result': 'skipped'}})
        outputs['inputs'] = json.dumps({**inputs, 'versions': False})
        self.reject()
        self.versions_selected = 'false'
        self.reject()
        self.selections['versions'] = 'false'
        self.assertTrue(self.verify())

    def test_archived_peers_keep_dedicated_schedule(self):
        self.selections = {name: 'true' for name in self.selections}
        self.expected = {name: 'true' if name == 'diagnostics' else 'false' for name in self.expected}
        for name in self.expected:
            if name != 'diagnostics':
                self.data[name] = {'result': 'skipped', 'outputs': {}}
        inputs = {name: name == 'versions' for name in
                  ('lint', 'python_37', 'asan', 'coverage', 'static_analysis', 'versions')}
        jobs = {name: {'result': 'success' if chosen else 'skipped'} for name, chosen in inputs.items()}
        outputs = self.data['diagnostics']['outputs']
        outputs.update(jobs=json.dumps(jobs), inputs=json.dumps(inputs))
        self.assertIn('diagnostics/versions: success', self.verify(scheduled=True))
        del jobs['versions'], inputs['versions']
        outputs.update(jobs=json.dumps(jobs), inputs=json.dumps(inputs))
        with self.assertRaises(ValueError):
            self.verify(scheduled=True)

    def test_change_selections_are_complete_booleans(self):
        original = self.selections
        for value in ({}, {'unknown': 'true'}, {**original, 'linux': ''},
                      {**original, 'linux': True}):
            with self.subTest(value=value):
                self.selections = value
                self.reject()
        self.selections = original

    def test_selected_families_must_run(self):
        self.selections['linux'] = 'false'
        self.reject()
        self.selections = {name: 'true' for name in self.selections}
        self.assertTrue(self.verify(scheduled=True))
        self.selections['asan'] = 'false'
        with self.assertRaises(ValueError):
            self.verify(scheduled=True)

    def test_failed_job_cannot_be_advisory(self):
        outputs = self.data['diagnostics']['outputs']
        jobs = json.loads(outputs['jobs'])
        jobs['static_analysis']['result'] = 'failure'
        outputs['jobs'] = json.dumps(jobs)
        outputs['advisory'] = 'static_analysis'
        self.reject()

    def test_interop_aggregate_is_required_even_when_endpoints_finish(self):
        outputs = self.data['interoperability']['outputs']
        jobs = json.loads(outputs['jobs'])
        for name in jobs:
            with self.subTest(missing=name):
                outputs['jobs'] = json.dumps({key: value for key, value in jobs.items() if key != name})
                self.reject()
        outputs['jobs'] = json.dumps({**jobs, 'result': {'result': 'skipped'}})
        outputs['inputs'] = json.dumps({'result': False})
        self.reject()
        del outputs['inputs']
        for endpoint in ({'blocked': 'true'}, {}, None):
            with self.subTest(endpoint=endpoint):
                jobs['linux']['outputs'] = endpoint
                outputs['jobs'] = json.dumps(jobs)
                self.reject()

    def test_every_family_requires_nested_results(self):
        for name in ('linux', 'diagnostics', 'macos', 'other', 'android', 'archive', 'valgrind', 'interoperability'):
            original = self.data[name]['outputs']
            with self.subTest(family=name):
                self.data[name]['outputs'] = {}
                self.reject()
            self.data[name]['outputs'] = original
        outputs = self.data['macos']['outputs']
        for result in ('failure', 'cancelled', 'skipped', None):
            outputs['jobs'] = json.dumps({'macos': {'result': result}})
            self.reject()

    def test_change_selection_is_required(self):
        original = self.data['changes']
        for value in ('failure', 'cancelled', 'skipped', None):
            with self.subTest(result=value):
                self.data['changes'] = {'result': value, 'outputs': {}}
                self.reject()
        self.data.pop('changes')
        self.reject()
        self.data['changes'] = original

    def test_other_selections(self):
        names = ('cygwin', 'solaris')
        for bits in range(1, 4):
            inputs = {name: bool(bits & (1 << i)) for i, name in enumerate(names)}
            jobs = {name: {'result': 'success' if inputs[name] else 'skipped'} for name in names}
            self.data['other']['outputs'] = {'jobs': json.dumps(jobs), 'inputs': json.dumps(inputs)}
            self.assertTrue(self.verify())
            for name in names:
                original = jobs[name]['result']
                invalid = ('failure', 'cancelled', 'skipped', None) if inputs[name] else ('success', 'failure')
                for result in invalid:
                    with self.subTest(selections=bits, job=name, result=result):
                        jobs[name]['result'] = result
                        self.data['other']['outputs']['jobs'] = json.dumps(jobs)
                        self.reject()
                jobs[name]['result'] = original
            self.data['other']['outputs']['jobs'] = json.dumps(jobs)
        self.data['other']['outputs'] = {
            'jobs': json.dumps({name: {'result': 'skipped'} for name in names}),
            'inputs': json.dumps({name: False for name in names})}
        self.reject()

    def test_android_jobs(self):
        outputs = self.data['android']['outputs']
        jobs = json.loads(outputs['jobs'])
        for name in jobs:
            for result in ('failure', 'cancelled', 'skipped', None):
                with self.subTest(job=name, result=result):
                    outputs['jobs'] = json.dumps({**jobs, name: {'result': result}})
                    self.reject()
            outputs['jobs'] = json.dumps({key: value for key, value in jobs.items() if key != name})
            self.reject()

    def test_command(self):
        env = {name: value for name, value in os.environ.items() if not name.startswith('CI_EXPECT_')}
        env.update({f'CI_EXPECT_{name.upper()}': value for name, value in self.expected.items()})
        env['CI_RESULTS'] = json.dumps(self.data)
        env['CI_SELECTIONS'] = json.dumps(self.selections)
        env['CI_VERSIONS_SELECTED'] = self.versions_selected
        env['GITHUB_EVENT_NAME'] = 'pull_request'
        command = [sys.executable, '-S', str(checker)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = self.data['diagnostics']['outputs']
        jobs = json.loads(outputs['jobs'])
        outputs['jobs'] = json.dumps({**jobs, 'static_analysis': {'result': 'failure'}})
        result = subprocess.run(command, env={**env, 'CI_RESULTS': json.dumps(self.data)},
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('diagnostics/static_analysis: failure', result.stderr)
        result = subprocess.run(command, env={**env, 'CI_RESULTS': 'not JSON'},
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
