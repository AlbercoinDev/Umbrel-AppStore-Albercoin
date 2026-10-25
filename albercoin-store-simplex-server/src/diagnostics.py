"""Authenticated, bounded availability check with an independent Tor client.

Only allowlisted outcomes leave this module. No raw Tor/CLI output, URI or
credentials are written to the diagnostic state or logs.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time

from tor_control import control_config, query

BUILD = 'tor-check-1'
STEPS = [('local_tls', 'SMP local e identidad TLS'),
         ('probe_tor', 'Tor independiente conectado'),
         ('onion', 'Acceso a la dirección onion'),
         ('tls', 'Identidad TLS a través de Tor'),
         ('smp', 'Crear, asegurar y eliminar una cola SMP temporal')]
MESSAGES = {
    'local_unavailable': 'SMP local no responde. Revisa el estado del proceso.',
    'identity': 'La huella o el certificado no coincide con la identidad configurada.',
    'tls': 'La verificación TLS no se ha superado. No se aceptó un certificado sin verificar.',
    'tor_start': 'No se pudo iniciar el Tor independiente. Revisa espacio y permisos.',
    'tor_timeout': 'El Tor independiente no ha establecido circuitos dentro del tiempo límite.',
    'onion_unavailable': 'El Tor independiente no ha podido alcanzar el onion:5223. Puede no estar publicado o disponible todavía.',
    'authorization': 'SMP rechazó la autorización para crear colas. La contraseña no coincide con la configuración del relay.',
    'smp_timeout': 'La conexión TLS funciona, pero la prueba SMP agotó su tiempo límite.',
    'smp_failed': 'La prueba SMP no terminó correctamente. No se ha confirmado la autorización ni la limpieza de la cola.',
    'cancelled': 'Prueba cancelada; no se modificó la identidad del servidor.',
    'internal': 'No se pudo completar la prueba. Comprueba espacio disponible y vuelve a intentarlo.',
}


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code


def read_exact(connection, size):
    data = b''
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError('Incomplete SOCKS response')
        data += chunk
    return data


def onion_socket(port, host, timeout=45):
    # Never resolve an onion name using host DNS, and never accept arbitrary targets.
    if not re.fullmatch(r'[a-z2-7]{56}\.onion', host):
        raise ProbeError('identity')
    connection = socket.create_connection(('127.0.0.1', port), timeout=timeout)
    try:
        connection.sendall(b'\x05\x01\x00')
        if read_exact(connection, 2) != b'\x05\x00':
            raise OSError('SOCKS authentication failed')
        name = host.encode('ascii')
        connection.sendall(b'\x05\x01\x00\x03' + bytes([len(name)]) + name + (5223).to_bytes(2, 'big'))
        reply = read_exact(connection, 4)
        if reply[:2] != b'\x05\x00':
            raise OSError('SOCKS target unavailable')
        if reply[3] == 1:
            read_exact(connection, 4)
        elif reply[3] == 4:
            read_exact(connection, 16)
        elif reply[3] == 3:
            read_exact(connection, read_exact(connection, 1)[0])
        else:
            raise OSError('Invalid SOCKS response')
        read_exact(connection, 2)
        return connection
    except Exception:
        connection.close()
        raise


def tls_context(ca_pem, fingerprint):
    try:
        der = ssl.PEM_cert_to_DER_cert(ca_pem)
        actual = base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode().rstrip('=')
        if not secrets.compare_digest(actual, fingerprint.rstrip('=')):
            raise ProbeError('identity')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # SMP pins the CA identity; a clearnet/onion hostname change need not
        # reissue the online certificate. Chain validation remains mandatory.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=ca_pem)
        return context
    except (ValueError, ssl.SSLError):
        raise ProbeError('identity') from None


def protocol_result(output, returncode):
    if returncode == 0 and 'SMP server test passed' in output:
        return
    if re.search(r'\bAUTH\b', output) and 'CreateQueue' in output:
        raise ProbeError('authorization')
    if 'TIMEOUT' in output:
        raise ProbeError('smp_timeout')
    raise ProbeError('smp_failed')


class Diagnostics:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.worker = None
        self.last_started = -1000.0
        self.state = {'status': 'idle', 'steps': [], 'build': BUILD}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def invalidate(self):
        with self.lock:
            if self.state['status'] != 'running':
                self.state = {'status': 'idle', 'steps': [], 'build': BUILD}

    def start(self, target):
        with self.lock:
            if self.state['status'] == 'running' or time.monotonic() - self.last_started < 60:
                return False
            self.last_started = time.monotonic()
            self.cancelled.clear()
            self.state = {'status': 'running', 'build': BUILD,
                          'started_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                          'steps': [dict(id=key, label=label, status='pending') for key, label in STEPS]}
            self.worker = threading.Thread(target=self.run, args=(target,), daemon=True)
            self.worker.start()
            return True

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise ProbeError('cancelled')

    def step(self, key, action):
        self.check_cancelled()
        with self.lock:
            entry = next(item for item in self.state['steps'] if item['id'] == key)
            entry['status'] = 'running'
        started = time.monotonic()
        result = action()
        self.check_cancelled()
        with self.lock:
            entry.update(status='passed', seconds=round(time.monotonic() - started, 1))
        return result

    def wait_tor(self, child, directory):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            self.check_cancelled()
            if child.poll() is not None:
                raise ProbeError('tor_start')
            try:
                status = query(directory)
                if status['progress'] == 100 and status['circuit'] and status['socks_port']:
                    return status['socks_port']
            except OSError:
                pass
            self.cancelled.wait(1)
        raise ProbeError('tor_timeout')

    def protocol_test(self, target, socks_port, directory):
        password = ':' + target['password'] if target['password'] else ''
        uri = f"smp://{target['fingerprint']}{password}@{target['onion']}:5223"
        command = ['simplex-chat', '-d', str(directory / 'client'),
                   '--user-display-name', 'ServerProbe', '--server', uri, '--smp-proxy', 'never',
                   '--socks-proxy', f'127.0.0.1:{socks_port}', '--socks-mode', 'always',
                   '--execute-log', 'none', '-e', '/smp test ' + uri, '-t', '1']
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                self.check_cancelled()
                try:
                    output, _ = child.communicate(timeout=.5)
                    protocol_result(output, child.returncode)
                    return
                except subprocess.TimeoutExpired:
                    pass
            raise ProbeError('smp_timeout')
        finally:
            if child.poll() is None:
                try:
                    child.kill()
                except ProcessLookupError:
                    pass
            child.communicate()

    def run(self, target):
        child = None
        connection = None
        started = time.monotonic()
        failure = None
        try:
            context = tls_context(target['ca_pem'], target['fingerprint'])

            def local_tls():
                try:
                    with socket.create_connection(('127.0.0.1', 5224), timeout=10) as local:
                        with context.wrap_socket(local, server_hostname=target['onion']):
                            pass
                except ssl.SSLError:
                    raise ProbeError('tls') from None
                except OSError:
                    raise ProbeError('local_unavailable') from None

            self.step('local_tls', local_tls)
            directory = self.root / 'probe-tor'
            directory.mkdir(mode=0o700, exist_ok=True)
            config = directory / 'torrc'
            config.write_text(f'DataDirectory {directory}\nSocksPort auto\nClientOnly 1\nAvoidDiskWrites 1\nLog notice stdout\n' + control_config(directory))
            config.chmod(0o600)
            child = subprocess.Popen(['tor', '-f', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            socks_port = self.step('probe_tor', lambda: self.wait_tor(child, directory))

            def connect_onion():
                try:
                    return onion_socket(socks_port, target['onion'])
                except OSError:
                    raise ProbeError('onion_unavailable') from None

            connection = self.step('onion', connect_onion)

            def remote_tls():
                try:
                    with context.wrap_socket(connection, server_hostname=target['onion']):
                        pass
                except ssl.SSLError:
                    raise ProbeError('tls') from None
                except OSError:
                    raise ProbeError('onion_unavailable') from None

            self.step('tls', remote_tls)
            with tempfile.TemporaryDirectory(prefix='smp-probe-') as temporary:
                self.step('smp', lambda: self.protocol_test(target, socks_port, Path(temporary)))
        except ProbeError as error:
            failure = error.code
        except Exception:
            failure = 'internal'
        finally:
            if connection is not None:
                connection.close()
            if child is not None:
                if child.poll() is None:
                    try:
                        child.send_signal(signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                    except ProcessLookupError:
                        pass
                    child.wait()
            with self.lock:
                if failure:
                    entry = next((item for item in self.state['steps'] if item['status'] == 'running'), self.state['steps'][0])
                    entry.update(status='failed', detail=MESSAGES.get(failure, MESSAGES['internal']))
                self.state.update(status='failed' if failure else 'passed', code=failure,
                                  finished_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                  seconds=round(time.monotonic() - started, 1))
