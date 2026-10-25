"""Check the actual image entrypoint, reduced capabilities and browser flow."""
import secrets
import os
import subprocess


name = 'simplex-web-probe-' + secrets.token_hex(5)
password = secrets.token_urlsafe(24)
image = os.environ.get('SERVER_IMAGE', 'simplex-server:dev')


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=120)


probe = '''
import http.cookiejar, os, re, time, urllib.parse, urllib.request
jar = http.cookiejar.CookieJar()
http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
base = 'http://127.0.0.1:2193'
for _ in range(100):
    try:
        html = http.open(base + '/login', timeout=2).read().decode()
        break
    except OSError:
        time.sleep(.1)
else:
    raise AssertionError('Manager did not start')
assert http.open(base + '/static/style.css').status == 200
def token(html):
    return re.search(r'name="csrf" value="([a-f0-9]+)"', html).group(1)
data = urllib.parse.urlencode(dict(csrf=token(html), username='admin', password=os.environ['APP_PASSWORD'])).encode()
html = http.open(base + '/login', data).read().decode()
assert 'Configura tu servidor' in html
data = urllib.parse.urlencode(dict(csrf=token(html), mode='clearnet', host='127.0.0.1', private='on')).encode()
html = http.open(base + '/', data, timeout=60).read().decode()
assert 'proceso activo' in html and 'smp://' in html
qr = http.open(base + '/qr.svg')
assert qr.headers['Content-Type'].startswith('image/svg+xml')
assert b'<svg' in qr.read()
status = open('/proc/1/status').read()
assert re.search(r'Uid:\\s+1000\\s+1000', status), 'Manager did not drop privileges'
print('Packaged entrypoint: non-root manager, login, CSS, setup, SMP and QR OK')
'''
try:
    docker('run', '-d', '--name', name, '--cap-drop', 'ALL',
           '--cap-add', 'CHOWN', '--cap-add', 'SETUID', '--cap-add', 'SETGID',
           '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
           '-e', 'APP_PASSWORD=' + password, image)
    result = docker('exec', name, 'python3', '-c', probe, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr)
    print(result.stdout.strip())
finally:
    docker('stop', '--time', '110', name, check=False)
    docker('rm', name, check=False)
