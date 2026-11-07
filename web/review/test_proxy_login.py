import os
from pathlib import Path
import runpy
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase, override_settings


class ProxySettingsTest(SimpleTestCase):
    def test_environment_is_opt_in_and_origins_accept_commas(self):
        path = Path(__file__).resolve().parents[1] / "juris_web/settings.py"
        with patch.dict(os.environ, {}, clear=True):
            defaults = runpy.run_path(str(path))
        self.assertEqual(defaults["CSRF_TRUSTED_ORIGINS"], [])
        self.assertIsNone(defaults["SECURE_PROXY_SSL_HEADER"])
        self.assertFalse(defaults["SESSION_COOKIE_SECURE"])
        with patch.dict(os.environ, {
            "DJANGO_CSRF_TRUSTED_ORIGINS": " https://jurisai.farhouse.org, https://otro.example, ",
            "DJANGO_TRUST_PROXY_HEADERS": "true", "DJANGO_SECURE_COOKIES": "true",
        }):
            configured = runpy.run_path(str(path))
        self.assertEqual(configured["CSRF_TRUSTED_ORIGINS"], ["https://jurisai.farhouse.org", "https://otro.example"])
        self.assertEqual(configured["SECURE_PROXY_SSL_HEADER"], ("HTTP_X_FORWARDED_PROTO", "https"))
        self.assertTrue(configured["SESSION_COOKIE_SECURE"])
        self.assertTrue(configured["CSRF_COOKIE_SECURE"])


@override_settings(ALLOWED_HOSTS=["jurisai.farhouse.org"])
class ProxyLoginTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Cuenta solo en la base de pruebas descartable de Django.
        cls.credentials = {"username": "proxy-test-admin", "password": "isolated-test-password-482!"}
        User.objects.create_superuser(email="test@example.invalid", **cls.credentials)

    def form(self, path, forwarded=True):
        client = Client(enforce_csrf_checks=True, HTTP_HOST="jurisai.farhouse.org",
                        **({"HTTP_X_FORWARDED_PROTO": "https"} if forwarded else {}))
        self.assertEqual(client.get(path).status_code, 200)
        return client, {**self.credentials, "csrfmiddlewaretoken": client.cookies["csrftoken"].value}

    def test_proxy_login_reproduces_403_then_accepts_valid_csrf(self):
        for path in ("/accounts/login/", "/admin/login/"):
            with self.subTest(path=path):
                with override_settings(CSRF_TRUSTED_ORIGINS=[], SECURE_PROXY_SSL_HEADER=None):
                    client, data = self.form(path)
                    self.assertEqual(client.post(path, data, HTTP_ORIGIN="https://jurisai.farhouse.org").status_code, 403)
                with override_settings(
                    CSRF_TRUSTED_ORIGINS=["https://jurisai.farhouse.org"],
                    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
                    SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True,
                ):
                    client, data = self.form(path)
                    self.assertTrue(client.cookies["csrftoken"]["secure"])
                    self.assertEqual(client.post(path, data, HTTP_ORIGIN="https://untrusted.example").status_code, 403)
                    self.assertEqual(client.post(path, self.credentials, HTTP_ORIGIN="https://jurisai.farhouse.org").status_code, 403)
                    response = client.post(path, data, HTTP_ORIGIN="https://jurisai.farhouse.org")
                    self.assertEqual(response.status_code, 302)
                    self.assertTrue(response.wsgi_request.is_secure())
                    self.assertIn("_auth_user_id", client.session)
                    self.assertTrue(response.cookies["sessionid"]["secure"])

    @override_settings(CSRF_TRUSTED_ORIGINS=[], SECURE_PROXY_SSL_HEADER=None,
                       SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False)
    def test_direct_http_login_still_works_without_proxy_settings(self):
        client, data = self.form("/accounts/login/", forwarded=False)
        response = client.post("/accounts/login/", data, HTTP_ORIGIN="http://jurisai.farhouse.org")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response.wsgi_request.is_secure())
