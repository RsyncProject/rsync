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
                             other='true', versions='true', android='true', valgrind='true')
        self.data = {
            'linux': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'ubuntu': {'result': 'success'}, 'almalinux': {'result': 'skipped'}}),
                'inputs': json.dumps({'ubuntu': True, 'almalinux': False})}},
            'diagnostics': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'clang_18': {'result': 'success'}, 'clang_21': {'result': 'success'},
                                    'asan': {'result': 'skipped'}, 'python_37': {'result': 'success'}}),
                'inputs': json.dumps({'clang_18': True, 'clang_21': True, 'asan': False, 'python_37': True})}},
            'bsd': {'result': 'skipped', 'outputs': {}},
            'macos': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'macos': {'result': 'success'}})}},
            'other': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'cygwin': {'result': 'success'}, 'solaris': {'result': 'skipped'}}),
                'inputs': json.dumps({'cygwin': True, 'solaris': False})}},
            'versions': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'mix': {'result': 'success'}})}},
            'android': {'result': 'success', 'outputs': {
                'jobs': json.dumps({name: {'result': 'success'} for name in ('bionic', 'x86_64', 'termux', 'emulator')}),
                'inputs': json.dumps(dict(bionic=True, x86_64=True, termux=True, emulator=True))}},
            'valgrind': {'result': 'success', 'outputs': {
                'jobs': json.dumps({'test': {'result': 'success'}})}},
        }

    def reject(self):
        with self.assertRaises((TypeError, ValueError)):
            check(self.data, self.expected)

    def test_success(self):
        self.assertTrue(check(self.data, self.expected))

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
                self.data[name] = {'result': 'skipped', 'outputs': {}}
                self.assertTrue(check(self.data, self.expected))
                self.data[name] = {'result': 'success', 'outputs': {}}
                self.reject()
                self.data[name] = original
                self.expected[name] = 'false' if name == 'bsd' else 'true'
        self.expected = {name: 'false' for name in self.expected}
        self.data = {name: {'result': 'skipped', 'outputs': {}} for name in self.data}
        self.reject()
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
        for name in ('clang_18', 'clang_21'):
            for result in ('failure', 'cancelled', 'skipped', None):
                with self.subTest(job=name, result=result):
                    outputs['jobs'] = json.dumps({**jobs, name: {'result': result}})
                    self.reject()
            outputs['jobs'] = json.dumps({key: value for key, value in jobs.items() if key != name})
            self.reject()
        outputs['jobs'] = json.dumps({**jobs, 'clang_18': {'result': 'skipped'}})
        outputs['inputs'] = json.dumps({'clang_18': False, 'clang_21': True, 'asan': False})
        self.assertTrue(check(self.data, self.expected))

    def test_failed_job_cannot_be_advisory(self):
        outputs = self.data['diagnostics']['outputs']
        jobs = json.loads(outputs['jobs'])
        jobs['clang_21']['result'] = 'failure'
        outputs['jobs'] = json.dumps(jobs)
        outputs['advisory'] = 'clang_21'
        self.reject()

    def test_every_family_requires_nested_results(self):
        for name in ('linux', 'diagnostics', 'macos', 'other', 'versions', 'android', 'valgrind'):
            original = self.data[name]['outputs']
            with self.subTest(family=name):
                self.data[name]['outputs'] = {}
                self.reject()
            self.data[name]['outputs'] = original
        outputs = self.data['macos']['outputs']
        for result in ('failure', 'cancelled', 'skipped', None):
            outputs['jobs'] = json.dumps({'macos': {'result': result}})
            self.reject()

    def test_other_selections(self):
        names = ('cygwin', 'solaris')
        for bits in range(1, 4):
            inputs = {name: bool(bits & (1 << i)) for i, name in enumerate(names)}
            jobs = {name: {'result': 'success' if inputs[name] else 'skipped'} for name in names}
            self.data['other']['outputs'] = {'jobs': json.dumps(jobs), 'inputs': json.dumps(inputs)}
            self.assertTrue(check(self.data, self.expected))
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
        command = [sys.executable, '-S', str(checker)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = self.data['diagnostics']['outputs']
        jobs = json.loads(outputs['jobs'])
        outputs['jobs'] = json.dumps({**jobs, 'clang_21': {'result': 'failure'}})
        result = subprocess.run(command, env={**env, 'CI_RESULTS': json.dumps(self.data)},
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('diagnostics/clang_21: failure', result.stderr)
        result = subprocess.run(command, env={**env, 'CI_RESULTS': 'not JSON'},
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
