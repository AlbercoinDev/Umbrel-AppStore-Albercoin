"""Run ONLY inside a disposable image/container, never against existing data."""
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from server import Runtime

os.umask(0o077)
Path('/etc/opt/simplex').mkdir(parents=True, exist_ok=True)
Path('/var/opt/simplex').mkdir(parents=True, exist_ok=True)
assert not list(Path('/etc/opt/simplex').glob('*')), 'Use a fresh disposable container'
with tempfile.TemporaryDirectory() as tmp:
    runtime = Runtime(Path(tmp))
    try:
        runtime.apply({'mode': 'clearnet', 'host': 'smp.example.org', 'private': True})
        assert runtime.status()['smp'] and runtime.status()['clearnet']
        fp = Path('/etc/opt/simplex/fingerprint').read_bytes()
        context = ssl._create_unverified_context()
        with socket.create_connection(('127.0.0.1', 5223)) as plain:
            with context.wrap_socket(plain, server_hostname='smp.example.org') as tls:
                assert tls.version() in ('TLSv1.2', 'TLSv1.3')
        print('Clearnet: process and TLS handshake OK (not an external/client SMP test)')
        runtime.apply({'mode': 'tor', 'host': '', 'private': True})
        assert runtime.status()['smp'] and runtime.status()['tor']
        assert not runtime.status()['clearnet']
        try:
            socket.create_connection(('127.0.0.1', 5223), timeout=1).close()
        except OSError:
            pass
        else:
            raise AssertionError('Public gateway still open in Tor mode')
        onion = (Path(tmp) / 'tor/smp/hostname').read_bytes()
        runtime.stop()
        runtime.apply({'mode': 'both', 'host': 'smp.example.org', 'private': True})
        assert Path('/etc/opt/simplex/fingerprint').read_bytes() == fp
        assert (Path(tmp) / 'tor/smp/hostname').read_bytes() == onion
        assert runtime.status()['clearnet']
        print('Tor and mixed: processes OK; SMP/onion identities stable; public gateway toggles OK')
        cfg = Path('/etc/opt/simplex/smp-server.ini')
        unavailable = cfg.with_suffix('.unavailable')
        cfg.rename(unavailable)
        try:
            runtime.apply({'mode': 'clearnet', 'host': 'smp.example.org', 'private': True})
        except RuntimeError:
            pass
        else:
            raise AssertionError('Missing INI must not regenerate identity')
        assert not runtime.status()['smp'] and not runtime.status()['clearnet']
        assert Path('/etc/opt/simplex/fingerprint').read_bytes() == fp
        unavailable.rename(cfg)
        print('Incomplete identity: regeneration refused and services stopped (fail closed)')
    finally:
        runtime.stop()
        print('Clean shutdown completed')
