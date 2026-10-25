"""Small cookie-authenticated Tor control client; Unix socket only."""
import re
import socket
from pathlib import Path


def command(stream, text):
    stream.write(text.encode('ascii') + b'\r\n')
    stream.flush()
    result = {}
    for _ in range(100):
        line = stream.readline(16384).decode('utf-8', 'replace').rstrip('\r\n')
        if not line.startswith('250'):
            raise OSError('Tor control request failed')
        body = line[4:]
        if '=' in body:
            key, value = body.split('=', 1)
            result[key] = value
        if line[3:4] == ' ':
            return result
    raise OSError('Tor control response too long')


def query(directory):
    directory = Path(directory)
    cookie = (directory / 'control.authcookie').read_bytes()
    if len(cookie) != 32:
        raise OSError('Invalid Tor control cookie')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect(str(directory / 'control'))
        with connection.makefile('rwb') as stream:
            command(stream, 'AUTHENTICATE ' + cookie.hex())
            info = command(stream, 'GETINFO status/bootstrap-phase status/circuit-established net/listeners/socks')
    progress = re.search(r'\bPROGRESS=(\d+)', info.get('status/bootstrap-phase', ''))
    listener = re.search(r'"127\.0\.0\.1:(\d+)"', info.get('net/listeners/socks', ''))
    return {'progress': min(100, int(progress.group(1))) if progress else 0,
            'circuit': info.get('status/circuit-established') == '1',
            'socks_port': int(listener.group(1)) if listener else None}


def control_config(directory):
    directory = Path(directory)
    return (f'ControlSocket {directory}/control\nCookieAuthentication 1\n'
            f'CookieAuthFile {directory}/control.authcookie\n')
