"""Online disposable test: independent Tor, pinned TLS and actual SMP queue test."""
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from diagnostics import Diagnostics
from server import Runtime

os.umask(0o077)
Path('/etc/opt/simplex').mkdir(parents=True, exist_ok=True)
Path('/var/opt/simplex').mkdir(parents=True, exist_ok=True)
assert not list(Path('/etc/opt/simplex').glob('*')), 'Disposable container only'
with tempfile.TemporaryDirectory() as root:
    runtime = Runtime(Path(root))
    try:
        runtime.apply({'mode': 'tor', 'host': '', 'private': True})
        for _ in range(150):
            if runtime.status()['bootstrap']:
                break
            time.sleep(1)
        else:
            raise AssertionError('Hosting Tor did not establish circuits')
        fingerprint = Path('/etc/opt/simplex/fingerprint').read_text()
        onion = Path(root, 'tor/smp/hostname').read_text()
        assert runtime.start_diagnostics()
        runtime.diagnostics.worker.join(timeout=450)
        result = runtime.diagnostics.snapshot()
        print(result, flush=True)  # Allowlisted state only, no target or credentials.
        assert result['status'] == 'passed', 'Independent Tor availability test failed'
        assert all(step['status'] == 'passed' for step in result['steps'])
        assert Path('/etc/opt/simplex/fingerprint').read_text() == fingerprint
        assert Path(root, 'tor/smp/hostname').read_text() == onion
        wrong = Diagnostics(Path(root))
        assert wrong.start({'onion': onion.strip(), 'fingerprint': fingerprint.strip(),
                            'ca_pem': Path('/etc/opt/simplex/ca.crt').read_text(), 'password': 'wrong'})
        wrong.worker.join(timeout=450)
        result = wrong.snapshot()
        print(result, flush=True)
        assert result['status'] == 'failed' and result['code'] == 'authorization'
        print('Independent Tor: TLS pinned, SMP queue test passed, wrong password rejected, identities unchanged', flush=True)
    finally:
        runtime.stop()
