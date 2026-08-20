"""Coverage for the OS-keychain backend, using a stand-in keyring module.

The real keyring is an optional dependency and there is no keychain daemon on a
CI runner, so a fake module is installed into sys.modules for these tests. The
point is to prove TokenStore drives the keyring API correctly and never touches
the filesystem when that backend is active.
"""

import sys

import pytest

from m365broker.tokens import TokenSet, TokenStore

from .support import token_response


class FakeKeyring:
    def __init__(self):
        self.storage = {}

    def set_password(self, service, account, value):
        self.storage[(service, account)] = value

    def get_password(self, service, account):
        return self.storage.get((service, account))

    def delete_password(self, service, account):
        del self.storage[(service, account)]  # raises if absent, like the real one


@pytest.fixture
def fake_keyring(monkeypatch):
    module = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", module)
    return module


@pytest.fixture
def keyring_store(fake_keyring, tmp_path):
    return TokenStore(path=tmp_path / "unused.json", use_keyring=True)


def test_round_trip_through_the_keychain(keyring_store, fake_keyring):
    tokens = TokenSet.from_response(token_response())

    keyring_store.save(tokens)

    assert fake_keyring.storage  # it really went to the keyring
    assert keyring_store.load().access_token == tokens.access_token


def test_nothing_is_written_to_disk(keyring_store):
    keyring_store.save(TokenSet.from_response(token_response()))
    assert not keyring_store.path.exists()


def test_load_returns_none_when_the_keychain_is_empty(keyring_store):
    assert keyring_store.load() is None


def test_clear_removes_the_entry(keyring_store, fake_keyring):
    keyring_store.save(TokenSet.from_response(token_response()))
    keyring_store.clear()

    assert fake_keyring.storage == {}
    assert keyring_store.load() is None


def test_clear_is_safe_when_the_entry_is_already_gone(keyring_store):
    keyring_store.clear()  # the real keyring raises here; must be swallowed


def test_named_accounts_are_isolated(fake_keyring, tmp_path):
    """Multi-account support: two slots must not overwrite each other."""
    first = TokenStore(path=tmp_path / "a.json", account="work", use_keyring=True)
    second = TokenStore(path=tmp_path / "b.json", account="personal", use_keyring=True)

    first.save(TokenSet.from_response(token_response(access_token="work-token")))
    second.save(TokenSet.from_response(token_response(access_token="personal-token")))

    assert first.load().access_token == "work-token"
    assert second.load().access_token == "personal-token"


def test_corrupt_keychain_entry_raises_a_clear_error(keyring_store, fake_keyring):
    fake_keyring.storage[(TokenStore.SERVICE_NAME, "default")] = "{not json"

    with pytest.raises(Exception) as excinfo:
        keyring_store.load()

    assert "sign in again" in str(excinfo.value)


def test_backend_autodetect_prefers_the_keyring_when_importable(fake_keyring, tmp_path):
    store = TokenStore(path=tmp_path / "t.json")
    assert store.use_keyring is True


def test_backend_autodetect_falls_back_when_keyring_is_absent(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "keyring", None)
    store = TokenStore(path=tmp_path / "t.json")
    assert store.use_keyring is False
