import json
import os
import stat
import time

import pytest

from m365broker.errors import BrokerError
from m365broker.tokens import TokenSet, TokenStore

from .support import make_jwt, token_response


def test_from_response_sets_absolute_expiry():
    before = time.time()
    tokens = TokenSet.from_response(token_response(expires_in=3600))
    assert before + 3599 <= tokens.expires_at <= time.time() + 3601


def test_from_response_extracts_the_account_from_the_id_token():
    tokens = TokenSet.from_response(token_response())
    assert tokens.account["username"] == "ada@contoso.onmicrosoft.com"
    assert tokens.account["name"] == "Ada Lovelace"


def test_account_falls_back_to_upn_when_preferred_username_absent():
    payload = token_response(id_token=make_jwt({"upn": "legacy@contoso.com"}))
    assert TokenSet.from_response(payload).account["username"] == "legacy@contoso.com"


def test_malformed_id_token_does_not_break_sign_in():
    """A bad id_token costs us display info, not the session."""
    tokens = TokenSet.from_response(token_response(id_token="not-a-jwt"))
    assert tokens.access_token == "access-token-1"
    assert tokens.account == {"name": None, "username": None, "oid": None, "tid": None}


def test_rotation_keeps_the_previous_refresh_token_when_none_returned():
    """Microsoft may omit refresh_token on renewal; we must not lose it."""
    first = TokenSet.from_response(token_response(refresh_token="rt-1"))
    payload = token_response(access_token="access-token-2")
    payload.pop("refresh_token")

    second = TokenSet.from_response(payload, previous=first)

    assert second.access_token == "access-token-2"
    assert second.refresh_token == "rt-1"


def test_rotation_takes_the_new_refresh_token_when_returned():
    first = TokenSet.from_response(token_response(refresh_token="rt-1"))
    second = TokenSet.from_response(token_response(refresh_token="rt-2"), previous=first)
    assert second.refresh_token == "rt-2"


def test_account_is_carried_over_when_renewal_returns_no_id_token():
    first = TokenSet.from_response(token_response())
    payload = token_response()
    payload.pop("id_token")
    second = TokenSet.from_response(payload, previous=first)
    assert second.account["username"] == "ada@contoso.onmicrosoft.com"


def test_is_expired_respects_the_skew():
    tokens = TokenSet(access_token="a", expires_at=time.time() + 60)
    assert tokens.is_expired(skew=120) is True   # inside the renew window
    assert tokens.is_expired(skew=10) is False   # still comfortably valid


def test_redacted_hides_every_secret_but_keeps_the_shape():
    tokens = TokenSet.from_response(token_response())
    view = tokens.redacted()

    blob = json.dumps(view)
    assert "access-token-1" not in blob
    assert "refresh-token-1" not in blob
    assert view["access_token"].startswith("<redacted:")
    assert view["account"]["username"] == "ada@contoso.onmicrosoft.com"
    assert "expires_in" in view


def test_store_round_trip(store):
    tokens = TokenSet.from_response(token_response())
    store.save(tokens)

    loaded = store.load()

    assert loaded.access_token == tokens.access_token
    assert loaded.refresh_token == tokens.refresh_token
    assert loaded.account == tokens.account
    assert loaded.expires_at == pytest.approx(tokens.expires_at)


def test_store_file_is_owner_only():
    """The fallback store's whole defence is file permissions - assert them."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        store = TokenStore(path=Path(tmp) / "tokens.json", use_keyring=False)
        store.save(TokenSet.from_response(token_response()))

        mode = stat.S_IMODE(os.stat(store.path).st_mode)

    assert mode == 0o600, f"expected 0600, found {oct(mode)}"


def test_store_leaves_no_temp_file_behind(store):
    store.save(TokenSet.from_response(token_response()))
    siblings = list(store.path.parent.iterdir())
    assert [p.name for p in siblings] == ["tokens.json"]


def test_load_returns_none_when_nothing_saved(store):
    assert store.load() is None


def test_clear_removes_the_session(store):
    store.save(TokenSet.from_response(token_response()))
    store.clear()
    assert store.load() is None


def test_clear_is_safe_when_already_empty(store):
    store.clear()  # must not raise


def test_corrupt_store_raises_a_clear_error(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json")

    with pytest.raises(BrokerError) as excinfo:
        store.load()

    assert "sign in again" in str(excinfo.value)
