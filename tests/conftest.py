import pytest

from m365broker.config import BrokerConfig
from m365broker.tokens import TokenStore

from .support import StubServer, token_response


@pytest.fixture
def stub():
    """A stub Microsoft with the standard token endpoint already wired."""
    with StubServer() as server:
        server.route("POST", "/common/oauth2/v2.0/token",
                     lambda query, body: (200, token_response()))
        yield server


@pytest.fixture
def config(stub):
    """Config pointed at the stub instead of login.microsoftonline.com."""
    return BrokerConfig(
        client_id="11111111-2222-3333-4444-555555555555",
        authority=stub.base_url,
        graph_base_url=f"{stub.base_url}/graph/v1.0",
        login_timeout=10.0,
        http_timeout=10.0,
    )


@pytest.fixture
def store(tmp_path):
    """A token store isolated to the test's own tmp dir.

    use_keyring is forced off: the suite must never touch the developer's real
    OS keychain, and CI runners have no keyring daemon at all.
    """
    return TokenStore(path=tmp_path / "tokens.json", use_keyring=False)
