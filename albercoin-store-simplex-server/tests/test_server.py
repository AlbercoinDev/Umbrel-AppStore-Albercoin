import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('server', Path(__file__).parents[1] / 'src/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class SettingsTests(unittest.TestCase):
    def test_tor_default(self):
        self.assertEqual(server.validate_settings({'private': 'on'}), {'mode': 'tor', 'host': '', 'private': True})

    def test_hosts(self):
        for host in ['smp.example.org', '1.2.3.4', 'my-node.example.org']:
            self.assertEqual(server.validate_settings({'mode': 'both', 'host': host})['host'], host)

    def test_invalid_hosts(self):
        for host in ['', 'https://example.org', 'example.org:5223', 'a.onion', 'a\nb.org', '-a.org', '::1', '1.2.3.999']:
            with self.subTest(host=host), self.assertRaises(ValueError):
                server.validate_settings({'mode': 'clearnet', 'host': host})

    def test_invalid_mode(self):
        with self.assertRaises(ValueError):
            server.validate_settings({'mode': 'anything'})


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = server.Runtime(Path(self.temp.name))
        with patch.dict(os.environ, APP_PASSWORD='test-password'):
            self.app = server.create_app(self.runtime)
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def csrf(self):
        self.client.get('/login')
        with self.client.session_transaction() as session:
            return session['csrf']

    def login(self):
        return self.client.post('/login', data={'csrf': self.csrf(), 'username': 'admin', 'password': 'test-password'})

    def test_auth_required(self):
        for path in ['/', '/qr.svg']:
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_csrf_required(self):
        self.assertEqual(self.client.post('/login', data={'password': 'test-password'}).status_code, 403)

    def test_login_and_logout(self):
        self.assertEqual(self.login().status_code, 302)
        page = self.client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'XFTP', page.data)
        self.assertIn('no-store', page.headers['Cache-Control'])
        with self.client.session_transaction() as session:
            csrf = session['csrf']
        self.client.post('/logout', data={'csrf': csrf})
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_login_rate_limit(self):
        token = self.csrf()
        for _ in range(10):
            self.client.post('/login', data={'csrf': token, 'password': 'wrong'})
        self.assertEqual(self.client.post('/login', data={'csrf': token}).status_code, 429)

    def test_settings_csrf_and_validation(self):
        self.login()
        with patch.object(self.runtime, 'apply') as apply:
            self.assertEqual(self.client.post('/', data={'mode': 'clearnet'}).status_code, 403)
            with self.client.session_transaction() as session:
                csrf = session['csrf']
            self.client.post('/', data={'csrf': csrf, 'mode': 'clearnet', 'host': 'evil\nsetting'})
            apply.assert_not_called()

    def test_password_is_hashed_and_secrets_persist(self):
        root = Path(self.temp.name)
        self.assertNotIn('test-password', (root / 'admin-hash').read_text())
        other = server.Runtime(root)
        self.assertEqual(other.password, self.runtime.password)
        self.assertEqual((root / 'queue-password').stat().st_mode & 0o777, 0o600)

    def test_missing_password_fails_closed(self):
        with patch.dict(os.environ, APP_PASSWORD=''), self.assertRaises(RuntimeError):
            server.create_app(self.runtime)

    def test_apply_failure_stops_partial_services(self):
        with patch.object(self.runtime, '_apply', side_effect=RuntimeError('fixture')), patch.object(self.runtime, 'stop') as stop:
            with self.assertRaises(RuntimeError):
                self.runtime.apply({'mode': 'tor', 'host': '', 'private': True})
            stop.assert_called_once()

    def test_health_has_no_credentials(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.json, {'manager': 'ok'})
        self.assertNotIn(self.runtime.password.encode(), response.data)

    def test_login_styles_are_public(self):
        response = self.client.get('/static/style.css')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/css', response.content_type)
        response.close()


if __name__ == '__main__':
    unittest.main()
