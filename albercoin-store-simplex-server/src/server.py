"""Local process supervisor and authenticated browser setup for SMP.

SMP and Tor are child processes, never controlled through a Docker socket.
"""
import configparser
import io
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import socketserver
import subprocess
import threading
import time
from datetime import timedelta

from flask import Flask, abort, redirect, render_template, request, session, url_for, send_file
from werkzeug.security import check_password_hash, generate_password_hash


def validate_settings(form):
    mode = form.get('mode', 'tor')
    if mode not in ('tor', 'clearnet', 'both'):
        raise ValueError('Modalidad no válida.')
    host = form.get('host', '').strip().lower()
    if mode != 'tor':
        try:
            ipaddress.IPv4Address(host)
        except ValueError:
            if len(host) > 253 or not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host) or host.endswith('.onion'):
                raise ValueError('Introduce un dominio o IPv4, sin protocolo ni puerto.')
    else:
        host = ''
    return {'mode': mode, 'host': host, 'private': form.get('private') == 'on'}


def write_private(path, text):
    temp = path.with_suffix('.tmp')
    temp.write_text(text)
    temp.chmod(0o600)
    temp.replace(path)


class Forwarder(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(128)
        self.connections = set()
        self.connection_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        with self.connection_lock:
            self.connections.add(request)
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.connection_lock:
                self.connections.discard(request)
            self.slots.release()

    def server_close(self):
        with self.connection_lock:
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        super().server_close()


class Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(('127.0.0.1', 5224), timeout=5) as peer:
                self.request.setblocking(False)
                peer.setblocking(False)
                deadline = time.monotonic() + 3600
                while time.monotonic() < deadline:
                    ready, _, _ = select.select([self.request, peer], [], [], 30)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        target = peer if source is self.request else self.request
                        # Bounded blocking write: a stalled client must not retain a worker forever.
                        target.settimeout(10)
                        target.sendall(data)
                        target.setblocking(False)
                        deadline = time.monotonic() + 3600
        except OSError:
            pass


class Runtime:
    def __init__(self, root):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.smp = self.tor = self.gateway = None
        self.error = ''
        self.tor_bootstrap = False
        self.settings = json.loads((root / 'settings.json').read_text()) if (root / 'settings.json').exists() else None
        self.password = self.secret('queue-password', lambda: secrets.token_urlsafe(24))

    def secret(self, name, factory):
        path = self.root / name
        if not path.exists():
            write_private(path, factory())
        return path.read_text().strip()

    def stop_child(self, name):
        child = getattr(self, name)
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT if name == 'smp' else signal.SIGTERM)
            try:
                child.wait(timeout=90 if name == 'smp' else 15)
            except subprocess.TimeoutExpired:
                # Do not kill SMP: a failed clean stop must block reconfiguration.
                raise RuntimeError('El proceso no ha terminado limpiamente. Revisa el estado antes de continuar.')
        setattr(self, name, None)

    def stop(self):
        if self.gateway:
            self.gateway.shutdown()
            self.gateway.server_close()
            self.gateway = None
        self.stop_child('smp')
        self.stop_child('tor')

    def apply(self, settings):
        with self.lock:
            try:
                self._apply(settings)
            except Exception:
                # Fail closed: never leave a half-applied public service running.
                self.stop()
                raise

    def capture_tor(self, child, path):
        handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=1)
        try:
            for line in child.stdout:
                if child is self.tor and 'Bootstrapped 100%' in line:
                    self.tor_bootstrap = True
                handler.emit(logging.LogRecord('tor', logging.INFO, '', 0, line.rstrip(), (), None))
        finally:
            handler.close()
            child.stdout.close()

    def _apply(self, settings):
        with self.lock:
            self.stop()
            self.error = ''
            self.tor_bootstrap = False
            tor_enabled = settings['mode'] != 'clearnet'
            if tor_enabled:
                tor_dir = self.root / 'tor'
                tor_dir.mkdir(exist_ok=True, mode=0o700)
                onion_dir = tor_dir / 'smp'
                torrc = self.root / 'torrc'
                write_private(torrc, f'DataDirectory {tor_dir}\nSocksPort 127.0.0.1:9050\nLog notice stdout\nHiddenServiceDir {onion_dir}\nHiddenServicePort 5223 127.0.0.1:5224\n')
                self.tor = subprocess.Popen(['tor', '-f', str(torrc)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                threading.Thread(target=self.capture_tor, args=(self.tor, tor_dir / 'notice.log'), daemon=True).start()
                for _ in range(100):
                    if (onion_dir / 'hostname').exists():
                        break
                    if self.tor.poll() is not None:
                        raise RuntimeError('Tor no ha podido iniciarse.')
                    time.sleep(.1)
                if not (onion_dir / 'hostname').exists():
                    raise RuntimeError('Tor no ha generado la identidad onion. Reintenta sin borrar los datos.')
            hosts = []
            if settings['mode'] != 'tor':
                hosts.append(settings['host'])
            if tor_enabled:
                hosts.append((self.root / 'tor/smp/hostname').read_text().strip())
            cfg = Path('/etc/opt/simplex/smp-server.ini')
            if not cfg.exists():
                if any(Path('/etc/opt/simplex').iterdir()) or any(Path('/var/opt/simplex').iterdir()):
                    raise RuntimeError('Hay datos SMP sin configuración. Restaura una copia; no se regenerará la identidad.')
                result = subprocess.run(['smp-server', 'init', '--yes', '--store-log', '--no-password', '--fqdn', hosts[0]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                if result.returncode:
                    raise RuntimeError('No se ha podido inicializar SMP. No borres certificados para reintentar.')
            ini = configparser.ConfigParser(interpolation=None)
            ini.read(cfg)
            for section in ('TRANSPORT', 'AUTH', 'WEB', 'PROXY', 'STORE_LOG'):
                if not ini.has_section(section):
                    ini.add_section(section)
            ini['TRANSPORT']['host'] = ','.join(hosts)
            ini['TRANSPORT']['port'] = '5224'
            ini['TRANSPORT'].pop('control_port', None)
            ini['TRANSPORT']['websockets'] = 'off'
            for key in ('http', 'https', 'cert', 'key'):
                ini['WEB'].pop(key, None)
            ini['AUTH']['new_queues'] = 'on'
            if settings['private']:
                ini['AUTH']['create_password'] = self.password
            else:
                ini['AUTH'].pop('create_password', None)
            ini['STORE_LOG']['enable'] = 'on'
            ini['STORE_LOG']['restore_messages'] = 'on'
            ini['PROXY'].pop('socks_proxy', None)
            ini['PROXY']['host_mode'] = 'onion' if settings['mode'] == 'tor' else 'public'
            if tor_enabled:
                ini['PROXY']['socks_proxy'] = '127.0.0.1:9050'
                ini['PROXY']['socks_mode'] = 'always' if settings['mode'] == 'tor' else 'onion'
            out = io.StringIO()
            ini.write(out)
            write_private(cfg, out.getvalue())
            self.smp = subprocess.Popen(['smp-server', 'start', '+RTS', '-N2', '-RTS'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(100):
                if self.smp.poll() is not None:
                    raise RuntimeError('SMP ha terminado durante el arranque. Comprueba configuración, permisos y espacio.')
                try:
                    with socket.create_connection(('127.0.0.1', 5224), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('SMP no ha abierto su puerto interno.')
            if settings['mode'] != 'tor':
                self.gateway = Forwarder(('0.0.0.0', 5223), Tunnel)
                threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
            write_private(self.root / 'settings.json', json.dumps(settings))
            self.settings = settings

    def status(self):
        onion_file = self.root / 'tor/smp/hostname'
        onion = onion_file.read_text().strip() if onion_file.exists() else ''
        fingerprint = Path('/etc/opt/simplex/fingerprint')
        address = ''
        if self.settings and fingerprint.exists():
            hosts = []
            if self.settings['mode'] != 'tor':
                hosts.append(self.settings['host'])
            if self.settings['mode'] != 'clearnet' and onion:
                hosts.append(onion)
            credential = ':' + self.password if self.settings['private'] else ''
            address = f"smp://{fingerprint.read_text().strip()}{credential}@{','.join(hosts)}:5223"
        bootstrap = self.tor_bootstrap and self.tor is not None and self.tor.poll() is None
        return dict(settings=self.settings, address=address, error=self.error,
                    smp=self.smp is not None and self.smp.poll() is None,
                    tor=self.tor is not None and self.tor.poll() is None,
                    bootstrap=bootstrap, clearnet=self.gateway is not None)


def create_app(runtime):
    app = Flask(__name__, root_path=str(Path(__file__).parent))
    app.secret_key = runtime.secret('session-key', lambda: secrets.token_hex(32))
    password = os.environ.get('APP_PASSWORD', '')
    if not password:
        raise RuntimeError('APP_PASSWORD es obligatorio; no se habilita un panel sin credenciales.')
    auth_hash = runtime.secret('admin-hash', lambda: generate_password_hash(password))
    app.config.update(MAX_CONTENT_LENGTH=8192, SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    attempts = {}
    attempt_lock = threading.Lock()

    @app.before_request
    def guard():
        if request.endpoint in ('health', 'static'):
            return
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', ''), session['csrf']):
            abort(403)
        if request.endpoint != 'login' and not session.get('authenticated'):
            return redirect(url_for('login'))

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; form-action 'self'; frame-ancestors 'none'"
        return response

    @app.route('/healthz')
    def health():
        return {'manager': 'ok'}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = ''
        if request.method == 'POST':
            # Global bound also prevents bypass via forged proxy headers or unbounded IP keys.
            with attempt_lock:
                now = time.monotonic()
                attempts['recent'] = [t for t in attempts.get('recent', []) if now - t < 60]
                if len(attempts['recent']) >= 10:
                    abort(429)
                attempts['recent'].append(now)
            if request.form.get('username') == 'admin' and check_password_hash(auth_hash, request.form.get('password', '')):
                session.clear()
                session.update(authenticated=True, csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect('/')
            error = 'Credenciales incorrectas.'
        return render_template('login.html', error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    @app.route('/', methods=['GET', 'POST'])
    def index():
        error = ''
        if request.method == 'POST':
            try:
                settings = validate_settings(request.form)
                runtime.apply(settings)
                return redirect('/')
            except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired, configparser.Error):
                error = 'No se pudieron aplicar los ajustes. Comprueba dominio, puertos, permisos y estado. Los datos no se han borrado.'
                runtime.error = error
        return render_template('index.html', **runtime.status(), form_error=error)

    @app.get('/qr.svg')
    def qr():
        import qrcode
        import qrcode.image.svg
        address = runtime.status()['address']
        if not address:
            abort(404)
        data = io.BytesIO()
        qrcode.make(address, image_factory=qrcode.image.svg.SvgPathImage).save(data)
        data.seek(0)
        return send_file(data, mimetype='image/svg+xml')

    return app


if __name__ == '__main__':
    os.umask(0o077)
    runtime = Runtime(Path(os.environ.get('DATA_DIR', '/data')))
    app = create_app(runtime)
    if runtime.settings:
        try:
            runtime.apply(runtime.settings)
        except Exception:
            runtime.error = 'No se pudo restaurar el servicio. Revisa ajustes y datos; no se regenerará la identidad.'

    def shutdown(signum, frame):
        with runtime.lock:
            runtime.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    # Use a production WSGI server, without access logs containing request data.
    from waitress import serve
    serve(app, host='0.0.0.0', port=2193, threads=4, channel_timeout=30)
