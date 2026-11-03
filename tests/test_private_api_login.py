import json
from types import SimpleNamespace

import pytest

from insta_crawler.private_api import PrivateApiCrawler, read_sessionid_from_browser_session


class FakeInstagrapiClient:
    def __init__(self, fail_with_missing_key: bool = False):
        self.fail_with_missing_key = fail_with_missing_key
        self.calls = []
        self.username = None
        self.public = SimpleNamespace(get=lambda url: SimpleNamespace(status_code=429, headers={"content-type": "application/json"}, text='{"message":"Please wait a few minutes","status":"fail"}'))

    def login(self, username, password):
        self.calls.append(("login", username))
        if self.fail_with_missing_key:
            raise TypeError("int() argument must be a string, a bytes-like object or a real number, not 'NoneType'")
        self.username = username

    def login_by_sessionid(self, sessionid):
        self.calls.append(("login_by_sessionid", sessionid))
        self.username = "from_session"

    def dump_settings(self, path):
        path.write_text("{}")


def build_crawler(tmp_path, monkeypatch, fake_client, **crawler_options):
    for variable_name in ("IG_USERNAME", "IG_PASSWORD", "IG_SESSIONID", "IG_BROWSER_SESSION"):
        monkeypatch.delenv(variable_name, raising=False)
    private_crawler = PrivateApiCrawler(session_directory=tmp_path, **crawler_options)
    private_crawler.client = fake_client
    return private_crawler


def test_missing_encryption_key_becomes_readable_error(tmp_path, monkeypatch):
    private_crawler = build_crawler(tmp_path, monkeypatch, FakeInstagrapiClient(fail_with_missing_key=True), username="burner", password="secret")
    with pytest.raises(RuntimeError) as raised_error:
        private_crawler.ensure_login()
    error_text = str(raised_error.value)
    assert "HTTP 429" in error_text and "Please wait a few minutes" in error_text


class FakeKeyClient:
    def __init__(self, key_on_route: str | None):
        self.key_on_route = key_on_route
        self.last_response = None
        self.public = SimpleNamespace(get=lambda url: SimpleNamespace(status_code=404, headers={"Content-Type": "text/html"}, text="<html>"))

    def respond(self, route_name):
        key_headers = {"ig-set-password-encryption-key-id": "41", "ig-set-password-encryption-pub-key": "UFVCS0VZ"} if route_name == self.key_on_route else {}
        self.last_response = SimpleNamespace(status_code=200, headers=key_headers)

    def sync_device_features(self, login=False):
        self.respond("qe_sync_post")

    def sync_launcher(self, login=False):
        self.respond("launcher_sync")


def test_encryption_key_found_on_app_style_post(tmp_path, monkeypatch):
    private_crawler = build_crawler(tmp_path, monkeypatch, FakeKeyClient("qe_sync_post"), username="burner", password="secret")
    assert private_crawler.resilient_password_publickeys() == (41, "UFVCS0VZ")
    assert private_crawler.public_key_attempts == ["GET qe/sync: HTTP 404"]


def test_encryption_key_falls_through_to_launcher(tmp_path, monkeypatch):
    private_crawler = build_crawler(tmp_path, monkeypatch, FakeKeyClient("launcher_sync"), username="burner", password="secret")
    assert private_crawler.resilient_password_publickeys() == (41, "UFVCS0VZ")


def test_encryption_key_missing_everywhere_lists_attempts(tmp_path, monkeypatch):
    private_crawler = build_crawler(tmp_path, monkeypatch, FakeKeyClient(None), username="burner", password="secret")
    with pytest.raises(RuntimeError) as raised_error:
        private_crawler.resilient_password_publickeys()
    assert "GET qe/sync: HTTP 404 | POST qe/sync (app style): HTTP 200 | POST launcher/sync (app style): HTTP 200" in str(raised_error.value)


def test_patch_is_installed_on_real_client(tmp_path, monkeypatch):
    for variable_name in ("IG_SESSIONID", "IG_BROWSER_SESSION"):
        monkeypatch.delenv(variable_name, raising=False)
    private_crawler = PrivateApiCrawler(username="burner", password="secret", session_directory=tmp_path)
    assert private_crawler.client.password_publickeys == private_crawler.resilient_password_publickeys


def test_sessionid_from_browser_session_skips_password(tmp_path, monkeypatch):
    browser_session_path = tmp_path / "burner_a.json"
    browser_session_path.write_text(json.dumps({"cookies": [
        {"name": "csrftoken", "domain": ".instagram.com", "value": "x"},
        {"name": "sessionid", "domain": ".instagram.com", "value": "6123456789%3AabcDEF%3A12%3AAYf"},
    ]}))
    assert read_sessionid_from_browser_session(browser_session_path) == "6123456789%3AabcDEF%3A12%3AAYf"
    fake_client = FakeInstagrapiClient()
    private_crawler = build_crawler(tmp_path, monkeypatch, fake_client, browser_session_path=browser_session_path)
    private_crawler.ensure_login()
    assert fake_client.calls == [("login_by_sessionid", "6123456789%3AabcDEF%3A12%3AAYf")]
    assert (tmp_path / "6123456789.json").exists()


def test_no_credentials_explains_options(tmp_path, monkeypatch):
    for variable_name in ("IG_USERNAME", "IG_PASSWORD", "IG_SESSIONID", "IG_BROWSER_SESSION"):
        monkeypatch.delenv(variable_name, raising=False)
    with pytest.raises(RuntimeError) as raised_error:
        PrivateApiCrawler(session_directory=tmp_path)
    assert "IG_SESSIONID" in str(raised_error.value)
