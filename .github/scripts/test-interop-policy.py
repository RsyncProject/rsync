import copy
import unittest

from interop_policy import PORTS, QUARANTINE, TAILNET, admission, update_policy

ROLES = ('a', 'b')


class Policy(unittest.TestCase):
    def setUp(self):
        self.context = dict(run_id='123', run_attempt='1', source_sha='a' * 40)
        self.manifest = dict(self.context, tailnet=TAILNET, repository_id='266877886', nodes={})
        self.devices = []
        for i, role in enumerate(ROLES, 1):
            ip, node_id = f'100.64.0.{i}', f'node-{i}'
            self.manifest['nodes'][role] = dict(address=ip, node_id=node_id)
            self.devices.append(dict(nodeId=node_id, addresses=[ip], tags=[QUARANTINE], authorized=True))

    def test_admission(self):
        grant = admission(self.manifest, self.context, self.devices, ROLES)
        self.assertEqual(len(grant['src']), 2)
        self.assertEqual(grant['src'], grant['dst'])
        self.assertEqual(grant['ip'], PORTS)

    def test_wrong_context(self):
        for field, value in dict(run_id='124', run_attempt='2', source_sha='b' * 40,
                                 tailnet='other', repository_id='1').items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                admission(dict(self.manifest, **{field: value}), self.context, self.devices, ROLES)

    def test_endpoint_identity(self):
        for role in ROLES:
            manifest = copy.deepcopy(self.manifest)
            manifest['nodes'].pop(role)
            with self.subTest(role=role), self.assertRaises(ValueError):
                admission(manifest, self.context, self.devices, ROLES)
        for field, value in dict(address='127.0.0.1', node_id='node-2').items():
            manifest = copy.deepcopy(self.manifest)
            manifest['nodes']['a'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                admission(manifest, self.context, self.devices, ROLES)

    def test_provider_state(self):
        for field, value in dict(authorized=False, tags=['tag:other'], addresses=['100.64.1.1']).items():
            devices = copy.deepcopy(self.devices)
            devices[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                admission(self.manifest, self.context, devices, ROLES)

    def test_other_run_is_preserved(self):
        grant = admission(self.manifest, self.context, self.devices, ROLES)
        other = dict(src=['100.64.1.1'], dst=['100.64.1.1'], ip=list(PORTS))
        policy = dict(acls=[], grants=[other])
        updated = update_policy(policy, grant)
        self.assertEqual(updated['grants'], [other, grant])
        self.assertEqual(policy['grants'], [other])
        self.assertEqual(update_policy(updated, grant, previous=grant, remove=True)['grants'], [other])
        with self.assertRaises(ValueError):
            update_policy(updated, grant)
        with self.assertRaises(ValueError):
            update_policy(policy, grant, previous=grant, remove=True)
        with self.assertRaises(ValueError):
            update_policy(updated, grant, previous=other, remove=True)
        with self.assertRaises(ValueError):
            update_policy(dict(acls=[], grants=[other, other]), grant)

    def test_broad_access_is_rejected(self):
        grant = admission(self.manifest, self.context, self.devices, ROLES)
        for policy in ({}, dict(acls=[{}]), dict(acls=[], ssh=[{}]),
                       dict(acls=[], grants=[dict(src=['*'], dst=['*'], ip=['*'])])):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                update_policy(policy, grant)


if __name__ == '__main__':
    unittest.main()
