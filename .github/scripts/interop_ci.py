import json
from functools import lru_cache
import os
import re
import time

from changes import select as select_changes
from interop_network import NetworkError, decode, download_records, fetch
from interop_policy import ROLES

API = 'https://api.github.com/repos/RsyncProject/rsync'
WORKFLOW = 'RsyncProject/rsync/.github/workflows/interoperability-tests.yml@'
bound = None
api_cache = {}


class NotSelected(NetworkError):
    pass


class PeerUnavailable(NetworkError):
    pass


def api(path, array=False):
    key = path, array
    cached = api_cache.get(key)
    try:
        body, headers = fetch(API + path, os.environ['GH_TOKEN'],
                              headers={'If-None-Match': cached[0]} if cached else {}, operation='Read CI identity')
    except NetworkError as error:
        if error.code == 304 and cached:
            return cached[1]
        raise
    if array:
        try:
            value = json.loads(body)
        except ValueError:
            raise NetworkError('Invalid JSON response') from None
        if not isinstance(value, list):
            raise NetworkError('Expected a JSON array')
    else:
        value = decode(body)
    if headers.get('ETag'):
        api_cache[key] = headers['ETag'], value
    return value


def pull_paths(run):
    requests = run.get('pull_requests')
    if not isinstance(requests, list) or len(requests) != 1:
        return None
    request = requests[0]
    if not isinstance(request, dict):
        return None
    number = request.get('number')
    base = request.get('base')
    head = request.get('head')
    if (type(number) is not int or number < 1
            or not isinstance(base, dict) or not isinstance(head, dict)
            or not isinstance(base.get('repo'), dict)
            or base.get('repo', {}).get('id') != 266877886
            or not re.fullmatch(r'[0-9a-f]{40}', base.get('sha', ''))
            or not re.fullmatch(r'[0-9a-f]{40}', head.get('sha', ''))):
        return None
    current = api('/pulls/' + str(number))
    if (not isinstance(current, dict) or current.get('number') != number
            or not isinstance(current.get('base'), dict)
            or not isinstance(current.get('head'), dict)
            or not isinstance(current.get('base', {}).get('repo'), dict)
            or current.get('base', {}).get('repo', {}).get('id') != 266877886
            or current.get('base', {}).get('sha') != base['sha']
            or current.get('head', {}).get('sha') != head['sha']):
        return None
    paths = []
    statuses = {'added', 'removed', 'modified', 'renamed', 'copied', 'changed', 'unchanged'}
    for page in range(1, 31):
        files = api('/pulls/' + str(number) + '/files?per_page=100&page=' + str(page), array=True)
        if not isinstance(files, list) or len(files) > 100:
            return None
        for item in files:
            if (not isinstance(item, dict) or item.get('status') not in statuses
                    or not isinstance(item.get('filename'), str)):
                return None
            if item['status'] == 'renamed':
                if not isinstance(item.get('previous_filename'), str):
                    return None
                paths.append(item['previous_filename'])
            paths.append(item['filename'])
        if len(files) < 100:
            return paths or None
    return None


def selected(run):
    if run['event'] != 'pull_request':
        return True
    paths = pull_paths(run)
    if paths is None:
        return True
    try:
        return select_changes(paths)['interoperability'] is True
    except (TypeError, ValueError):
        return True


@lru_cache(maxsize=1)
def source():
    if os.environ.get('GITHUB_REPOSITORY_ID') != '266877886':
        raise NetworkError('Unexpected CI repository')
    event_name = os.environ['GITHUB_EVENT_NAME']
    if event_name == 'workflow_run':
        with open(os.environ['GITHUB_EVENT_PATH']) as stream:
            event = json.load(stream)
        upstream = event['workflow_run']
        if event.get('action') != 'in_progress' or upstream.get('name') != 'CI':
            raise NetworkError('Unexpected triggering workflow')
        run_id, attempt = upstream['id'], upstream['run_attempt']
    elif event_name in ('pull_request', 'push', 'schedule', 'workflow_dispatch'):
        run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
    else:
        raise NetworkError('Unexpected CI event')
    if type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1:
        raise NetworkError('Invalid CI run identity')
    run = api('/actions/runs/' + str(run_id))
    if (run.get('name') == 'CI' and run.get('repository', {}).get('id') == 266877886
            and run.get('path') != '.github/workflows/ci.yml'):
        raise NotSelected('CI uses a different workflow')
    if (run.get('path') != '.github/workflows/ci.yml' or run.get('name') != 'CI'
            or run.get('id') != run_id or run.get('repository', {}).get('id') != 266877886 or run.get('run_attempt') != attempt
            or run.get('event') not in ('pull_request', 'push', 'schedule', 'workflow_dispatch')
            or not isinstance(run.get('display_title'), str) or not run['display_title']):
        raise NetworkError('CI run does not match its event')
    if not selected(run):
        raise NotSelected('CI has not selected interoperability')
    references = [item for item in run.get('referenced_workflows', []) if item.get('path', '').startswith(WORKFLOW)]
    deadline = time.monotonic() + 180
    while not references and run.get('status') in ('queued', 'in_progress') and time.monotonic() < deadline:
        time.sleep(5)
        run = api('/actions/runs/' + str(run_id))
        if run.get('run_attempt') != attempt:
            raise NetworkError('CI attempt changed during workflow selection')
        references = [item for item in run.get('referenced_workflows', []) if item.get('path', '').startswith(WORKFLOW)]
    if len(references) != 1 or not re.fullmatch(r'[0-9a-f]{40}', references[0].get('sha', '')):
        if not references:
            raise NotSelected('CI has not selected the interoperability workflow')
        raise NetworkError('CI has an invalid interoperability workflow selection')
    if run.get('status') == 'completed':
        raise NotSelected('CI has already completed')
    return dict(run_id=str(run_id), run_attempt=str(attempt), source_sha=references[0]['sha'],
                head_sha=run['head_sha'], display_title=run['display_title'])


def identity():
    if bound is not None:
        return dict(bound)
    state = source()
    return {key: state[key] for key in ('run_id', 'run_attempt', 'source_sha')}


def bind(record):
    global bound
    state = {key: record.get(key) for key in ('run_id', 'run_attempt', 'source_sha')}
    if (any(not isinstance(state[key], str) or not re.fullmatch(r'[1-9][0-9]*', state[key]) for key in ('run_id', 'run_attempt'))
            or not isinstance(state['source_sha'], str) or not re.fullmatch(r'[0-9a-f]{40}', state['source_sha'])):
        raise NetworkError('Invalid journal identity')
    bound = state


def artifact_name(kind, role=None):
    return 'interoperability-' + identity()['run_attempt'] + '-' + kind + ('-' + role if role else '')


def validate(record, state=None):
    state = identity() if state is None else state
    if not isinstance(record, dict) or any(record.get(key) != value for key, value in state.items()):
        raise NetworkError('Record belongs to a different CI run, attempt or revision')
    return record


def records(kind, roles=ROLES, run_id=None, timeout=600):
    state = identity()
    expected = {artifact_name(kind, role): role for role in roles}
    checked = [0]
    def active():
        if time.monotonic() - checked[0] < 30:
            return
        checked[0] = time.monotonic()
        current = api('/actions/runs/' + state['run_id'])
        if (current.get('run_attempt') != int(state['run_attempt']) or current.get('conclusion') == 'cancelled'
                or current.get('status') == 'completed'):
            raise NetworkError('CI was cancelled or superseded')
        jobs = api('/actions/runs/' + state['run_id'] + '/attempts/' + state['run_attempt'] + '/jobs?per_page=100')
        count = jobs.get('total_count')
        if type(count) is not int or not 0 <= count <= 1000:
            raise NetworkError('CI job listing exceeds limit')
        listing = list(jobs.get('jobs', []))
        for page in range(2, (count + 99) // 100 + 1):
            listing += api('/actions/runs/' + state['run_id'] + '/attempts/' + state['run_attempt']
                           + '/jobs?per_page=100&page=' + str(page))['jobs']
        failed = [job['name'][len('Interoperability / '):] for job in listing
                  if job.get('name', '').startswith('Interoperability / ') and job.get('conclusion') in (
                      'failure', 'cancelled', 'timed_out', 'action_required')]
        if failed:
            raise PeerUnavailable('Unavailable endpoints: ' + ', '.join(failed))
    return {role: validate(record, state) for role, (record, _) in
            download_records(run_id or state['run_id'], expected, timeout, progress=active).items()}


def find_controller(exclude=None):
    state = source()
    titles = {f"Network controller: {state['display_title']}",
              f"Network CI {state['run_id']} attempt {state['run_attempt']}"}
    runs = api('/actions/workflows/interoperability.yml/runs?branch=master&event=workflow_run&per_page=100')['workflow_runs']
    for run in sorted(runs, key=lambda value: value['id']):
        if run.get('display_title') not in titles or str(run['id']) == exclude:
            continue
        if (run.get('path') != '.github/workflows/interoperability.yml'
                or run.get('head_branch') != 'master' or run.get('event') != 'workflow_run'
                or run.get('repository', {}).get('id') != 266877886):
            raise NetworkError('Controller does not match the trusted workflow revision')
        name = 'interoperability-context-' + str(run['run_attempt'])
        listing = api('/actions/runs/' + str(run['id']) + '/artifacts?per_page=100')
        if type(listing.get('total_count')) is not int or not 0 <= listing['total_count'] <= 100:
            raise NetworkError('Controller artifact listing exceeds limit')
        matches = [item for item in listing['artifacts'] if item.get('name') == name and item.get('expired') is False]
        if not matches:
            continue
        if len(matches) != 1:
            raise NetworkError('Duplicate controller context')
        metadata = download_records(str(run['id']), {name: None}, timeout=60)[None][0]
        try:
            validate(metadata, {key: state[key] for key in ('run_id', 'run_attempt', 'source_sha')})
        except NetworkError:
            continue
        if (metadata.get('control_run_id') != str(run['id'])
                or metadata.get('control_attempt') != str(run['run_attempt'])
                or metadata.get('control_sha') != run['head_sha']):
            raise NetworkError('Controller context has a different signing revision')
        return run
    return None


def controller():
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        run = find_controller()
        if run is not None:
            if run.get('conclusion') in ('failure', 'cancelled', 'timed_out'):
                raise PeerUnavailable('Network controller failed')
            return str(run['id'])
        time.sleep(5)
    raise NetworkError('Trusted controller did not start')


def published(kind, timeout=600):
    return records(kind, (None,), controller(), timeout)[None]


def write(root, kind, **data):
    path = root / (kind + '.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(identity(), **data), sort_keys=True) + '\n')
    temporary.replace(path)
