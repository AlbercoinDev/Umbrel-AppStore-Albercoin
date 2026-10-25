import base64
import hashlib
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))
from diagnostics import Diagnostics, ProbeError, STEPS, onion_socket, protocol_result, tls_context
from tor_control import command, query


class DiagnosticTests(unittest.TestCase):
    def test_protocol_success(self):
        protocol_result('Current user: ServerProbe\nSMP server test passed', 0)

    def test_protocol_errors_sanitized(self):
        for output, code in [('SMP server test failed at CreateQueue, error: SECRET AUTH PASSWORD', 'authorization'), ('BROKER TIMEOUT SECRET', 'smp_timeout'), ('SMP server test failed at Connect, error: SECRET HOST', 'smp_connect'), ('SMP server test failed at Connect, error: SECRET TRANSPORT', 'smp_tls'), ('SECRET other', 'smp_failed')]:
            with self.subTest(code=code), self.assertRaises(ProbeError) as error:
                protocol_result(output, 1)
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('SECRET', str(error.exception))

    def test_identity_must_match(self):
        with patch('diagnostics.ssl.PEM_cert_to_DER_cert', return_value=b'fixture'):
            with self.assertRaises(ProbeError) as error:
                tls_context('pem', 'wrong')
            self.assertEqual(error.exception.code, 'identity')

    def test_tls_does_not_disable_chain_validation(self):
        fingerprint = base64.urlsafe_b64encode(hashlib.sha256(b'fixture').digest()).decode()
        with patch('diagnostics.ssl.PEM_cert_to_DER_cert', return_value=b'fixture'), patch('diagnostics.ssl.SSLContext') as factory:
            context = tls_context('pem', fingerprint)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            context.load_verify_locations.assert_called_once_with(cadata='pem')

    def test_socks_uses_domain_not_host_dns(self):
        connection = MagicMock()
        data = bytearray(b'\x05\x00\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
        def recv(size):
            result = bytes(data[:size])
            del data[:size]
            return result
        connection.recv.side_effect = recv
        host = 'a' * 56 + '.onion'
        with patch('diagnostics.socket.create_connection', return_value=connection) as connect:
            self.assertIs(onion_socket(19050, host), connection)
            connect.assert_called_once_with(('127.0.0.1', 19050), timeout=45)
        packet = connection.sendall.call_args_list[1].args[0]
        self.assertEqual(packet[:4], b'\x05\x01\x00\x03')
        self.assertIn(host.encode(), packet)
        self.assertEqual(packet[-2:], (5223).to_bytes(2, 'big'))

    def test_arbitrary_socks_targets_forbidden(self):
        with self.assertRaises(ProbeError), patch('diagnostics.socket.create_connection') as connect:
            onion_socket(9050, 'example.org')
        connect.assert_not_called()

    def test_cooldown_and_single_flight(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            with patch('diagnostics.threading.Thread'):
                self.assertTrue(probe.start({'password': 'SECRET'}))
                self.assertFalse(probe.start({'password': 'SECRET'}))
                self.assertNotIn('SECRET', str(probe.snapshot()))
                probe.state['status'] = 'passed'
                self.assertFalse(probe.start({'password': 'SECRET'}))

    def test_snapshot_is_detached(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            state = probe.snapshot()
            state['status'] = 'running'
            self.assertEqual(probe.snapshot()['status'], 'idle')

    def test_tor_start_failure_is_assigned_to_correct_phase(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            probe.state = {'status': 'running', 'steps': [dict(id=key, label=label, status='pending') for key, label in STEPS]}
            with patch('diagnostics.tls_context', return_value=MagicMock()), patch('diagnostics.socket.create_connection'), patch('diagnostics.Path.mkdir', side_effect=OSError('SECRET raw filesystem error')):
                probe.run({'ca_pem': 'pem', 'fingerprint': 'fp', 'onion': 'fixture.onion'})
            result = probe.snapshot()
            self.assertEqual(result['code'], 'tor_start')
            self.assertEqual(result['steps'][0]['status'], 'passed')
            self.assertEqual(result['steps'][1]['status'], 'failed')
            self.assertNotIn('SECRET', str(result))

    def test_onion_transport_retries_are_visible(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            probe.state['steps'] = [dict(id='onion', status='running')]
            connection = MagicMock()
            with patch('diagnostics.onion_socket', side_effect=[OSError('SECRET'), connection]) as connect, patch.object(probe.cancelled, 'wait'), patch('diagnostics.time.monotonic', return_value=0):
                self.assertIs(probe.connect_onion(19050, 'fixture.onion'), connection)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(probe.snapshot()['steps'][0]['attempts'], 2)
            self.assertNotIn('SECRET', str(probe.snapshot()))

    def test_onion_transport_retry_is_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            probe.state['steps'] = [dict(id='onion', status='running')]
            with patch('diagnostics.onion_socket', side_effect=OSError('SECRET')), patch('diagnostics.time.monotonic', side_effect=[0, 0, 100]), self.assertRaises(ProbeError) as error:
                probe.connect_onion(19050, 'fixture.onion')
            self.assertEqual(error.exception.code, 'onion_unavailable')

    def test_cli_retries_only_explicit_connect_failure(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            probe.state['steps'] = [dict(id='smp', status='running')]
            first, second = MagicMock(), MagicMock()
            first.communicate.return_value = ('SMP server test failed at Connect, error: SECRET HOST', None)
            second.communicate.return_value = ('SMP server test passed', None)
            for child in (first, second):
                child.returncode = 0
                child.poll.return_value = 0
            with patch('diagnostics.subprocess.Popen', side_effect=[first, second]) as launch, patch('diagnostics.time.monotonic', return_value=0), patch.object(probe.cancelled, 'wait'):
                probe.protocol_test({'password': 'SECRET', 'fingerprint': 'fp', 'onion': 'fixture.onion'}, 9050, Path(root))
            self.assertEqual(launch.call_count, 2)
            self.assertEqual(probe.snapshot()['steps'][0]['attempts'], 2)
            self.assertNotIn('SECRET', str(probe.snapshot()))

    def test_cli_does_not_retry_queue_auth_failure(self):
        with tempfile.TemporaryDirectory() as root:
            probe = Diagnostics(root)
            probe.state['steps'] = [dict(id='smp', status='running')]
            child = MagicMock()
            child.communicate.return_value = ('SMP server test failed at CreateQueue, error: AUTH', None)
            child.returncode = 0
            child.poll.return_value = 0
            with patch('diagnostics.subprocess.Popen', return_value=child) as launch, patch('diagnostics.time.monotonic', return_value=0), self.assertRaises(ProbeError) as error:
                probe.protocol_test({'password': 'SECRET', 'fingerprint': 'fp', 'onion': 'fixture.onion'}, 9050, Path(root))
            self.assertEqual(launch.call_count, 1)
            self.assertEqual(error.exception.code, 'authorization')

    def test_control_errors_not_returned(self):
        stream = MagicMock()
        stream.readline.return_value = b'515 SECRET password rejected\r\n'
        with self.assertRaises(OSError) as error:
            command(stream, 'GETINFO status/bootstrap-phase')
        self.assertNotIn('SECRET', str(error.exception))

    def test_control_parses_live_status(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'control.authcookie').write_bytes(b'a' * 32)
            stream = MagicMock()
            stream.readline.side_effect = [b'250 OK\r\n', b'250-status/bootstrap-phase=NOTICE BOOTSTRAP PROGRESS=100 TAG=done\r\n', b'250-status/circuit-established=1\r\n', b'250-net/listeners/socks="127.0.0.1:19050"\r\n', b'250 OK\r\n']
            with patch('tor_control.socket.socket') as factory:
                factory.return_value.__enter__.return_value.makefile.return_value.__enter__.return_value = stream
                self.assertEqual(query(root), {'progress': 100, 'circuit': True, 'socks_port': 19050})
                factory.assert_called_once_with(socket.AF_UNIX, socket.SOCK_STREAM)


if __name__ == '__main__':
    unittest.main()
