"""Optional local Docker probe. Uses an existing CLI image, never production data.

CLIENT_IMAGE=simplex-chat-cli:1.0.4-test python3 tests/probe_client.py
"""
import os
from pathlib import Path
import secrets
import subprocess
import time


def docker(*args, check=True):
    return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=120)


name = 'simplex-probe-' + secrets.token_hex(5)
root = str(Path(__file__).resolve().parents[1])
image = os.environ['CLIENT_IMAGE']
use_tor = os.environ.get('PROBE_TOR') == '1'
fixture = '''
import os, sys, signal, time
from pathlib import Path
sys.path.insert(0, '/workspace/src')
from server import Runtime
os.umask(0o077)
Path('/etc/opt/simplex').mkdir(parents=True, exist_ok=True)
Path('/var/opt/simplex').mkdir(parents=True, exist_ok=True)
r = Runtime(Path('/tmp/fixture'))
def stop(*args):
    r.stop()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
r.apply(dict(mode='tor' if os.environ.get('PROBE_TOR') == '1' else 'clearnet', host='127.0.0.1', private=True))
if os.environ.get('PROBE_TOR') == '1':
    for _ in range(150):
        if r.status()['bootstrap']: break
        time.sleep(1)
    else: raise RuntimeError('Tor bootstrap timed out')
Path('/tmp/fixture-address').write_text(r.status()['address'])
while True: time.sleep(1)
'''
try:
    docker('run', '-d', '--name', name, '-e', 'PROBE_TOR=' + ('1' if use_tor else '0'), '--entrypoint', 'python3', '-v', root + ':/workspace:ro', 'simplex-server:dev', '-c', fixture)
    for _ in range(360 if use_tor else 60):
        ready = docker('exec', name, 'cat', '/tmp/fixture-address', check=False)
        if ready.returncode == 0:
            break
        time.sleep(.5)
    else:
        raise RuntimeError('Disposable server did not become ready')
    uri = ready.stdout.strip()
    for valid in (True, False):
        target = uri if valid else uri.split(':', 2)[0] + ':' + uri.split(':', 2)[1] + ':wrong@' + uri.split('@', 1)[1]
        proxy_args = ['--socks-proxy', '127.0.0.1:9050', '--socks-mode', 'onion'] if use_tor else []
        result = docker('run', '--rm', '--network', 'container:' + name,
                        '--entrypoint', 'simplex-chat', image,
                        '-d', '/tmp/probe', '--user-display-name', 'Probe',
                        '--smp-proxy', 'never', *proxy_args, '-e', '/smp test ' + target,
                        '-t', '2', check=False)
        output = result.stdout + result.stderr
        if valid:
            assert 'SMP server test passed' in output, 'Valid credential probe failed'
        else:
            assert 'AUTH' in output and 'CreateQueue' in output, 'Invalid credential was not rejected'
        print(('Valid credential accepted' if valid else 'Invalid credential rejected at queue creation'))
    result = docker('run', '--rm', '--network', 'container:' + name, '-v', root + ':/workspace:ro',
                    '--entrypoint', '/opt/venv/bin/python', image, '/workspace/tests/client_exchange.py', uri, check=False)
    if result.returncode:
        # Assertions and stack traces only; fixture content/links are not printed by the client runner.
        raise RuntimeError(result.stderr.replace(uri, '[URI]'))
    print(result.stdout.strip())
finally:
    docker('stop', '--time', '110', name, check=False)
    docker('rm', name, check=False)
