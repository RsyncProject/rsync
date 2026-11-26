import base64
import hashlib
import json
import os
import re
import subprocess

from interop_network import NetworkError, require_no_oidc


def openssl(args, data=None):
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT') if key in os.environ}
    try:
        result = subprocess.run(['openssl', *args], input=data, capture_output=True, env=env, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise NetworkError('Enrolment cryptography could not complete') from None
    if result.returncode:
        raise NetworkError('Enrolment cryptography failed')
    return result.stdout


def label(role, state):
    value = json.dumps(dict(state, role=role), sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(value).hexdigest()


def protect(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)


def generate(root, role, state):
    require_no_oidc()
    protect(root)
    private = root / 'enrol-private.pem'
    with private.open('xb') as stream:
        os.chmod(private, 0o600)
        stream.write(openssl(['genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072']))
    public = openssl(['pkey', '-in', str(private), '-pubout']).decode('ascii')
    record = dict(state, role=role, public_key=public)
    (root / 'public-key.json').write_text(json.dumps(record) + '\n')


def seal(root, role, record, key_id, key, state):
    if any(record.get(k) != v for k, v in dict(state, role=role).items()):
        raise NetworkError('Enrolment public key has stale context')
    public = record.get('public_key')
    if not isinstance(public, str) or not 100 < len(public) < 2048:
        raise NetworkError('Invalid enrolment public key')
    path = root / ('public-' + role + '.pem')
    path.write_text(public)
    description = openssl(['pkey', '-pubin', '-in', str(path), '-text', '-noout']).decode('ascii')
    if not re.search(r'^(?:RSA )?Public-Key: \(3072 bit\)$', description, re.M):
        raise NetworkError('Unexpected enrolment public key size')
    encrypted = openssl(['pkeyutl', '-encrypt', '-pubin', '-inkey', str(path),
                         '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256',
                         '-pkeyopt', 'rsa_mgf1_md:sha256', '-pkeyopt', 'rsa_oaep_label:' + label(role, state)], key.encode())
    return dict(state, role=role, key_id=key_id, public_key_sha256=hashlib.sha256(public.encode()).hexdigest(),
                encrypted_key=base64.b64encode(encrypted).decode())


def decrypt(root, role, record, state):
    if any(record.get(k) != v for k, v in dict(state, role=role).items()):
        raise NetworkError('Enrolment key has stale context')
    public = json.loads((root / 'public-key.json').read_text())['public_key']
    if record.get('public_key_sha256') != hashlib.sha256(public.encode()).hexdigest():
        raise NetworkError('Enrolment key has the wrong recipient')
    encoded = record.get('encrypted_key')
    if not isinstance(encoded, str) or len(encoded) != 512:
        raise NetworkError('Invalid encrypted enrolment key')
    cipher = base64.b64decode(encoded, validate=True)
    key = openssl(['pkeyutl', '-decrypt', '-inkey', str(root / 'enrol-private.pem'),
                   '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256',
                   '-pkeyopt', 'rsa_mgf1_md:sha256', '-pkeyopt', 'rsa_oaep_label:' + label(role, state)], cipher)
    if not key.startswith(b'tskey-auth-') or len(key) > 256 or any(char < 33 or char > 126 for char in key):
        raise NetworkError('Invalid decrypted enrolment key')
    with (root / 'enrol-key').open('xb') as stream:
        os.chmod(root / 'enrol-key', 0o600)
        stream.write(key)
    (root / 'enrolment.json').write_text(json.dumps({k: v for k, v in record.items() if k != 'encrypted_key'}) + '\n')


def forget(root):
    for name in ('enrol-private.pem', 'enrol-key'):
        try:
            (root / name).unlink()
        except FileNotFoundError:
            pass
