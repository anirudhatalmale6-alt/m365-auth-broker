import pytest

from m365broker.config import DEFAULT_SCOPES, BrokerConfig
from m365broker.errors import ConfigError


def test_endpoints_follow_the_v2_shape():
    config = BrokerConfig(client_id="abc", tenant="organizations")
    assert config.authorize_endpoint.endswith("/organizations/oauth2/v2.0/authorize")
    assert config.token_endpoint.endswith("/organizations/oauth2/v2.0/token")


def test_default_scopes_cover_the_requested_permissions():
    """offline_access is what makes silent renewal possible - guard it."""
    assert "offline_access" in DEFAULT_SCOPES
    assert "Mail.Read" in DEFAULT_SCOPES
    assert "Mail.Send" in DEFAULT_SCOPES


def test_client_id_is_required():
    with pytest.raises(ConfigError):
        BrokerConfig(client_id="")


def test_redirect_path_must_be_rooted():
    with pytest.raises(ConfigError):
        BrokerConfig(client_id="abc", redirect_path="callback")


def test_scopes_accept_a_space_separated_string():
    config = BrokerConfig(client_id="abc", scopes="Mail.Read Mail.Send")
    assert config.scopes == ["Mail.Read", "Mail.Send"]
    assert config.scope_string == "Mail.Read Mail.Send"


def test_trailing_slashes_do_not_produce_double_slash_urls():
    config = BrokerConfig(client_id="abc", authority="https://example.com/")
    assert "//organizations" not in config.authorize_endpoint.replace("https://", "")


def test_from_env_reads_the_documented_variables(monkeypatch):
    monkeypatch.setenv("M365_CLIENT_ID", "from-env")
    monkeypatch.setenv("M365_TENANT", "contoso.onmicrosoft.com")
    monkeypatch.setenv("M365_SCOPES", "Mail.Read offline_access")
    monkeypatch.setenv("M365_REDIRECT_PORT", "5580")

    config = BrokerConfig.from_env()

    assert config.client_id == "from-env"
    assert config.tenant == "contoso.onmicrosoft.com"
    assert config.scopes == ["Mail.Read", "offline_access"]
    assert config.redirect_port == 5580


def test_from_env_overrides_win(monkeypatch):
    monkeypatch.setenv("M365_CLIENT_ID", "from-env")
    assert BrokerConfig.from_env(client_id="explicit").client_id == "explicit"
