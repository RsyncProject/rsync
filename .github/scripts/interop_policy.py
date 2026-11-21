import copy
import ipaddress
import re

TAILNET = 'TG2FEugs2S11CNTRL'
QUARANTINE = 'tag:quarantine'
ROLES = ('linux', 'freebsd', 'netbsd', 'openbsd', 'macos', 'solaris', 'cygwin')
PORTS = ['tcp:18731', 'tcp:18733']


def address(value):
    parsed = ipaddress.ip_address(value)
    if not isinstance(value, str) or not isinstance(parsed, ipaddress.IPv4Address) or parsed not in ipaddress.ip_network('100.64.0.0/10'):
        raise ValueError('Expected an allocated Tailscale IPv4 address')
    return str(parsed)


def admission(manifest, context, devices, roles):
    if set(context) != {'run_id', 'run_attempt', 'source_sha'}:
        raise ValueError('Unexpected workflow context')
    expected = dict(context, repository_id='266877886', tailnet=TAILNET)
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError('Readiness manifest does not match the workflow context')
    for key in ('run_id', 'run_attempt'):
        if not isinstance(context.get(key), str) or not re.fullmatch(r'[1-9][0-9]*', context[key]):
            raise ValueError('Invalid workflow run identity')
    if not isinstance(context.get('source_sha'), str) or not re.fullmatch(r'[0-9a-f]{40}', context['source_sha']):
        raise ValueError('Invalid source revision')
    nodes = manifest.get('nodes')
    if not isinstance(nodes, dict) or set(nodes) != set(roles):
        raise ValueError('Missing or unexpected endpoint role')
    ids, addresses = set(), set()
    for role in roles:
        node = nodes[role]
        if not isinstance(node, dict) or set(node) != {'node_id', 'address'}:
            raise ValueError('Invalid endpoint identity')
        node_id, ip = node['node_id'], address(node['address'])
        if not isinstance(node_id, str) or not node_id or node_id in ids or ip in addresses:
            raise ValueError('Endpoint identities are not distinct')
        matches = [device for device in devices if device.get('nodeId') == node_id]
        if len(matches) != 1:
            raise ValueError('Endpoint is not uniquely registered in the tailnet')
        device = matches[0]
        if device.get('authorized') is not True or device.get('tags') != [QUARANTINE] or ip not in device.get('addresses', []):
            raise ValueError('Endpoint does not match its registered quarantine identity')
        ids.add(node_id)
        addresses.add(ip)
    ips = sorted(addresses)
    return {'src': ips, 'dst': ips, 'ip': list(PORTS)}


def validate_grant(grant):
    if not isinstance(grant, dict) or set(grant) != {'src', 'dst', 'ip'}:
        raise ValueError('Unexpected network grant')
    src, dst = grant['src'], grant['dst']
    if not isinstance(src, list) or not src or src != dst or grant['ip'] != PORTS:
        raise ValueError('Only cohort-local fixture ports are permitted')
    if len(set(src)) != len(src) or any(address(ip) != ip for ip in src):
        raise ValueError('Invalid cohort addresses')
    return set(src)


def check_policy(policy):
    if policy.get('acls') != [] or policy.get('ssh', []) != []:
        raise ValueError('An explicit deny-all baseline is required')
    for key in ('autoApprovers', 'nodeAttrs', 'ipsets'):
        if policy.get(key):
            raise ValueError('Unexpected network policy capabilities')
    grants = policy.get('grants', [])
    if not isinstance(grants, list):
        raise ValueError('Invalid grants')
    allocated = set()
    for existing in grants:
        ips = validate_grant(existing)
        if allocated & ips:
            raise ValueError('Existing cohorts overlap')
        allocated.update(ips)
    return grants


def update_policy(policy, grant, previous=None, remove=False):
    members = validate_grant(grant)
    grants = check_policy(policy)
    retained = copy.deepcopy(grants)
    if previous is not None:
        validate_grant(previous)
        if previous != grant:
            raise ValueError('The admission lease does not match this cohort')
        if retained.count(previous) != 1:
            raise ValueError('The recorded admission lease is missing or duplicated')
        retained.remove(previous)
    elif remove:
        raise ValueError('Cleanup requires the recorded admission lease')
    for existing in retained:
        if members & set(existing['src']):
            raise ValueError('Admission overlaps another cohort')
    if not remove:
        retained.append(copy.deepcopy(grant))
    result = copy.deepcopy(policy)
    result['grants'] = retained
    return result
