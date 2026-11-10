import json
import os
import sys


def check(data, expected):
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(isinstance(data, dict) and data.keys() == expected.keys(),
            'Missing or unexpected job families')
    require(all(value in ('true', 'false') for value in expected.values())
            and 'true' in expected.values(), 'No valid job selection')
    messages = []
    for family, group in data.items():
        require(isinstance(group, dict), f'{family}: invalid job results')
        result = group.get('result')
        if expected[family] == 'false':
            require(result == 'skipped', f'{family}: expected skipped')
            messages.append(f'{family}: skipped')
            continue
        require(result == 'success', f'{family}: not successful')
        outputs = group.get('outputs', {})
        require(isinstance(outputs, dict), f'{family}: invalid outputs')
        jobs = json.loads(outputs.get('jobs', 'null'))
        inputs = json.loads(outputs.get('inputs', '{}'))
        advisory = outputs.get('advisory')
        require(isinstance(jobs, dict) and jobs, f'{family}: missing job results')
        require(isinstance(inputs, dict) and all(type(value) is bool for value in inputs.values()),
                f'{family}: invalid job selections')
        require(inputs.keys() <= jobs.keys(), f'{family}: unknown job selection')
        require(advisory is None or isinstance(advisory, str) and advisory in jobs,
                f'{family}: unknown advisory job')
        require(any(inputs.get(name) is not False for name in jobs), f'{family}: no jobs selected')
        for name, job in jobs.items():
            require(isinstance(job, dict), f'{family}/{name}: invalid job result')
            result = job.get('result')
            if inputs.get(name) is False:
                require(result == 'skipped', f'{family}/{name}: expected skipped')
            else:
                require(result == 'success' or result == 'failure' and name == advisory,
                        f'{family}/{name}: {result or "missing result"}')
            messages.append(f'{family}/{name}: {result}')
    return messages


def main():
    try:
        expected = {name.removeprefix('CI_EXPECT_').lower(): value for name, value in os.environ.items()
                    if name.startswith('CI_EXPECT_')}
        messages = check(json.loads(os.environ['CI_RESULTS']), expected)
    except (KeyError, TypeError, ValueError) as error:
        print(f'CI result check failed: {error}', file=sys.stderr)
        return 1
    print('\n'.join(messages))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
