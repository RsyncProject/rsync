import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from interop_policy import QUARANTINE, TAILNET

CLIENT_ID = 'TG2FEugs2S11CNTRL-kx3fW8WXMt11CNTRL'
SLOTS = ('a1', 'a2', 'b1', 'b2')
TAILNET_API = 'https://api.tailscale.com/api/v2/tailnet/-'


class NetworkError(ValueError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def decode(body):
    try:
        result = json.loads(body)
    except ValueError:
        raise NetworkError('Invalid JSON response') from None
    if not isinstance(result, dict):
        raise NetworkError('Expected a JSON object')
    return result


def fetch(url, token=None, data=None, headers=None, allowed_redirect=False, method=None, operation='Network request'):
    options = {'Accept': 'application/json', **(headers or {})}
    if token:
        options['Authorization'] = 'Bearer ' + token
    try:
        with urllib.request.build_opener(NoRedirect()).open(
                urllib.request.Request(url, data=data, headers=options, method=method), timeout=30) as response:
            body = response.read(1048577)
            if len(body) > 1048576:
                raise NetworkError(operation + ': response exceeds limit')
            return body, response.headers
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 302 and allowed_redirect:
            target = error.headers.get('Location', '')
            parts = urllib.parse.urlsplit(target)
            if (parts.scheme != 'https' or not (parts.hostname or '').endswith('.blob.core.windows.net')
                    or parts.username or parts.password or parts.port not in (None, 443)):
                raise NetworkError('Unexpected artifact download host') from None
            return fetch(target, operation=operation)
        raise NetworkError(operation + ': HTTP ' + str(error.code), error.code) from None
    except (OSError, urllib.error.URLError):
        raise NetworkError(operation + ': connection failed') from None


def context():
    env = os.environ
    event = workflow_event()
    source = event['workflow_run']
    if event['workflow']['path'] == '.github/workflows/interoperability.yml':
        path = os.path.join(env['RUNNER_TEMP'], 'interoperability-cleanup', 'context.json')
        with open(path, 'rb') as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise NetworkError('Cleanup context exceeds limit')
        metadata = decode(data)
        if (metadata.get('run_id') != str(source['id']) or metadata.get('run_attempt') != str(source['run_attempt'])
                or not re.fullmatch(r'[0-9a-f]{40}', metadata.get('control_sha', ''))):
            raise NetworkError('Cleanup context does not match its trusted workflow')
        result = {key: metadata[key] for key in ('run_id', 'run_attempt', 'source_sha')}
    else:
        result = dict(run_id=env['GITHUB_RUN_ID'], run_attempt=env['GITHUB_RUN_ATTEMPT'], source_sha=source['head_sha'])
    if (any(not re.fullmatch(r'[1-9][0-9]*', result[key]) for key in ('run_id', 'run_attempt'))
            or not re.fullmatch(r'[0-9a-f]{40}', result['source_sha'])):
        raise NetworkError('Invalid workflow identity')
    return result


def workflow_event():
    env = os.environ
    if (env.get('GITHUB_REPOSITORY_ID') != '266877886' or env.get('GITHUB_EVENT_NAME') != 'workflow_run'
            or env.get('GITHUB_REF') != 'refs/heads/master'):
        raise NetworkError('Unexpected CI context')
    with open(env['GITHUB_EVENT_PATH'], 'rb') as stream:
        data = stream.read(2097153)
    if len(data) > 2097152:
        raise NetworkError('Workflow event exceeds limit')
    event = decode(data)
    repository = event.get('repository', {})
    source = event.get('workflow_run', {})
    path = event.get('workflow', {}).get('path')
    if (repository.get('id') != 266877886 or repository.get('owner', {}).get('id') != 166180282
            or path not in ('.github/workflows/ci.yml', '.github/workflows/interoperability.yml')
            or type(source.get('id')) is not int or source['id'] <= 0
            or type(source.get('run_attempt')) is not int or source['run_attempt'] <= 0
            or not re.fullmatch(r'[0-9a-f]{40}', source.get('head_sha', ''))):
        raise NetworkError('Unexpected upstream workflow')
    if path == '.github/workflows/ci.yml':
        if (event.get('action') != 'in_progress' or source.get('name') != 'CI'
                or source.get('event') not in ('pull_request', 'push', 'schedule', 'workflow_dispatch')):
            raise NetworkError('Unsupported upstream CI event')
    elif (event.get('action') != 'completed' or source.get('name') != 'Interoperability'
          or source.get('event') != 'workflow_run'):
        raise NetworkError('Unsupported cleanup event')
    return event


def identity_token():
    context()
    parts = urllib.parse.urlsplit(os.environ['ACTIONS_ID_TOKEN_REQUEST_URL'])
    if (parts.scheme != 'https' or not (parts.hostname or '').endswith('.actions.githubusercontent.com')
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise NetworkError('Unexpected OIDC endpoint')
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != 'audience']
    query.append(('audience', 'api.tailscale.com/' + CLIENT_ID))
    url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
    data, _ = fetch(url, os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN'], operation='GitHub OIDC request')
    token = decode(data).get('value')
    if not isinstance(token, str) or not token or len(token) > 32768:
        raise NetworkError('Invalid OIDC response')
    return token


def exchange():
    data = urllib.parse.urlencode({'client_id': CLIENT_ID, 'jwt': identity_token()}).encode()
    body, _ = fetch('https://api.tailscale.com/api/v2/oauth/token-exchange', data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                    operation='Tailscale controller token exchange')
    token = decode(body).get('access_token')
    if not isinstance(token, str) or not token or len(token) > 32768:
        raise NetworkError('Invalid federation response')
    return token


def require_no_oidc():
    if any(os.environ.get(key) for key in ('ACTIONS_ID_TOKEN_REQUEST_URL', 'ACTIONS_ID_TOKEN_REQUEST_TOKEN')):
        raise NetworkError('Worker has OIDC request variables')


def worker_authority():
    require_no_oidc()
    validate_preflight(artifacts('preflight', timeout=300)[None])
    return False


def validate_preflight(record):
    if (any(record.get(key) != value for key, value in context().items())
            or record.get('api') != TAILNET_API or record.get('readable') != ['acl', 'devices']):
        raise NetworkError('Missing controller API positive control')


def artifact_name(kind, slot=None, attempt=None):
    suffix = '-' + slot if slot else ''
    return 'interoperability-' + (context()['run_attempt'] if attempt is None else str(attempt)) + '-' + kind + suffix


def artifacts(kind, slots=(None,), timeout=600):
    state = context()
    expected = {artifact_name(kind, slot): slot for slot in slots}
    records = download_records(state['run_id'], expected, timeout)
    result = {}
    for slot, (record, _) in records.items():
        if any(record.get(key) != value for key, value in state.items()):
            raise NetworkError('Stale rendezvous artifact')
        result[slot] = record
    return result


def download_records(run_id, expected, timeout=600):
    if not isinstance(run_id, str) or not re.fullmatch(r'[1-9][0-9]*', run_id):
        raise NetworkError('Invalid artifact source run')
    url = ('https://api.github.com/repos/RsyncProject/rsync/actions/runs/'
           + run_id + '/artifacts?per_page=100')
    deadline = time.monotonic() + timeout
    cached, etag = None, None
    while time.monotonic() < deadline:
        try:
            body, headers = fetch(url, os.environ['GH_TOKEN'], headers={'If-None-Match': etag} if etag else {},
                                  operation='List GitHub artifacts')
        except NetworkError as error:
            if error.code != 304 or cached is None:
                raise
            records = cached
        else:
            listing = decode(body)
            records = listing['artifacts']
            count = listing['total_count']
            if type(count) is not int or count < 0 or count > 1000:
                raise NetworkError('Artifact listing exceeds rendezvous limit')
            for page in range(2, (count + 99) // 100 + 1):
                body, _ = fetch(url + '&page=' + str(page), os.environ['GH_TOKEN'], operation='List GitHub artifacts')
                records += decode(body)['artifacts']
            records = list({item['id']: item for item in records}.values())
            cached, etag = records, headers.get('ETag')
        found = {name: [item for item in records if item['name'] == name and not item['expired']]
                 for name in expected}
        if any(len(items) > 1 for items in found.values()):
            raise NetworkError('Duplicate rendezvous artifact')
        if all(len(items) == 1 for items in found.values()):
            result = {}
            for name, items in found.items():
                artifact_id = items[0]['id']
                if type(artifact_id) is not int or artifact_id <= 0:
                    raise NetworkError('Invalid artifact identity')
                download = 'https://api.github.com/repos/RsyncProject/rsync/actions/artifacts/' + str(artifact_id) + '/zip'
                try:
                    data, _ = fetch(download, os.environ['GH_TOKEN'], allowed_redirect=True, operation='Download GitHub artifact')
                except NetworkError as error:
                    if error.code != 404:
                        raise
                    # The listing can precede archive availability.
                    result = None
                    break
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    files = [item for item in archive.infolist() if not item.is_dir()]
                    if len(files) != 1 or files[0].file_size > 65536:
                        raise NetworkError('Invalid rendezvous artifact')
                    raw = archive.read(files[0])
                result[expected[name]] = decode(raw), raw
            if result is not None:
                return result
        time.sleep(5)
    raise NetworkError('Timed out waiting for rendezvous artifacts')


class Tailnet:
    def __init__(self):
        if not CLIENT_ID.startswith(TAILNET + '-'):
            raise NetworkError('Controller credential does not match the configured test namespace')
        self.token = exchange()
        self.base = TAILNET_API

    def devices(self):
        body, _ = fetch(self.base + '/devices', self.token, operation='List Tailscale devices')
        devices = decode(body).get('devices')
        if not isinstance(devices, list) or any(not isinstance(device, dict) for device in devices):
            raise NetworkError('Invalid device list')
        return devices

    def policy(self):
        body, headers = fetch(self.base + '/acl', self.token, operation='Read Tailscale policy')
        return decode(body), headers

    def change(self, transform):
        for _ in range(5):
            policy, headers = self.policy()
            etag = headers.get('ETag')
            if not etag:
                raise NetworkError('Policy response has no concurrency token')
            changed = transform(policy)
            try:
                _, applied = fetch(self.base + '/acl', self.token, json.dumps(changed).encode(),
                                   {'Content-Type': 'application/hujson', 'If-Match': etag}, operation='Update Tailscale policy')
                return dict(before=etag, after=applied.get('ETag'))
            except NetworkError as error:
                if error.code != 412:
                    raise
        raise NetworkError('Policy changed during every admission attempt')

    def delete(self, device_id):
        if not isinstance(device_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', device_id):
            raise NetworkError('Invalid device API identity')
        try:
            fetch('https://api.tailscale.com/api/v2/device/' + device_id, self.token, method='DELETE',
                  operation='Delete Tailscale device')
        except NetworkError as error:
            if error.code != 404:
                raise

    def create_key(self, slot):
        if slot not in SLOTS:
            raise NetworkError('Unexpected enrolment role')
        state = context()
        description = 'ci-' + state['run_id'] + '-' + state['run_attempt'] + '-' + slot
        payload = dict(expirySeconds=900, description=description,
                       capabilities=dict(devices=dict(create=dict(reusable=False, ephemeral=True, preauthorized=True,
                                                                  tags=[QUARANTINE]))))
        body, _ = fetch(self.base + '/keys', self.token, json.dumps(payload).encode(),
                        {'Content-Type': 'application/json'}, operation='Create quarantine key')
        result = decode(body)
        if (not isinstance(result.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9]+', result['id'])
                or not isinstance(result.get('key'), str) or not result['key'].startswith('tskey-auth-')
                or len(result['key']) > 256):
            raise NetworkError('Invalid quarantine key response')
        return result['id'], result['key']

    def delete_key(self, key_id):
        if not isinstance(key_id, str) or not re.fullmatch(r'[A-Za-z0-9]+', key_id):
            raise NetworkError('Invalid enrolment key identity')
        try:
            fetch(self.base + '/keys/' + key_id, self.token, method='DELETE', operation='Revoke quarantine key')
        except NetworkError as error:
            if error.code != 404:
                raise
