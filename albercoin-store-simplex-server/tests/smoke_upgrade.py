"""Disposable initial-0.1.0 -> refined-0.1.0 upgrade, using private test volumes."""
import os
import secrets
import subprocess

old_image = 'ghcr.io/albercoindev/simplex-server:0.1.0@sha256:4a9c0324ea93a3ccbf5a5efd0d70d2936c073aa57c90a498b71ce1052e65cfb6'
new_image = os.environ.get('SERVER_IMAGE', 'simplex-server:dev')
prefix = 'simplex-upgrade-' + secrets.token_hex(5)
password = secrets.token_urlsafe(24)
volumes = [(prefix + '-manager', '/data'), (prefix + '-config', '/etc/opt/simplex'), (prefix + '-state', '/var/opt/simplex')]
containers = []


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=180)


probe = '''
import hashlib, http.cookiejar, json, os, re, time, urllib.parse, urllib.request
from pathlib import Path
http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base = 'http://127.0.0.1:2193'
for _ in range(150):
    try:
        html = http.open(base + '/login', timeout=2).read().decode()
        break
    except OSError: time.sleep(.1)
else: raise AssertionError('Manager did not start')
def token(html): return re.search(r'name="csrf" value="([a-f0-9]+)"', html).group(1)
data = urllib.parse.urlencode(dict(csrf=token(html), username='admin', password=os.environ['APP_PASSWORD'])).encode()
html = http.open(base + '/login', data).read().decode()
if os.environ['PROBE_STAGE'] == 'old':
    data = urllib.parse.urlencode(dict(csrf=token(html), mode='tor', private='on')).encode()
    html = http.open(base + '/', data, timeout=60).read().decode()
else:
    assert 'tor-check-1' in html
    assert json.load(http.open(base + '/service-status'))['smp']
assert 'smp://' in html
paths = ['/etc/opt/simplex/ca.crt', '/etc/opt/simplex/ca.key', '/etc/opt/simplex/server.crt', '/etc/opt/simplex/server.key', '/etc/opt/simplex/fingerprint', '/data/tor/smp/hostname', '/data/tor/smp/hs_ed25519_secret_key', '/data/queue-password', '/data/admin-hash', '/data/session-key']
print(json.dumps({path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}, sort_keys=True))
'''
try:
    for volume, _ in volumes:
        docker('volume', 'create', volume)
    previous = None
    for stage, image in [('old', old_image), ('new', new_image)]:
        name = prefix + '-' + stage
        containers.append(name)
        mounts = [argument for volume, path in volumes for argument in ['-v', volume + ':' + path]]
        docker('run', '-d', '--name', name, '-e', 'APP_PASSWORD=' + password, '-e', 'PROBE_STAGE=' + stage,
               '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID', '--cap-add', 'DAC_OVERRIDE',
               '--security-opt', 'no-new-privileges:true', *mounts, image)
        result = docker('exec', name, 'python3', '-c', probe, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr)
        if stage == 'old':
            previous = result.stdout
        else:
            assert result.stdout == previous, 'Identity or credentials changed during upgrade'
        docker('stop', '--time', '150', name)
        docker('rm', name)
        containers.remove(name)
    print('Initial 0.1.0 -> tor-check-1: SMP CA/fingerprint, onion/secret key, queue password, admin hash and session key unchanged; same login and SMP running')
finally:
    for name in containers:
        docker('stop', '--time', '150', name, check=False)
        docker('rm', name, check=False)
    for volume, _ in volumes:
        docker('volume', 'rm', volume, check=False)
