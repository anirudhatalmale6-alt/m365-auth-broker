import pytest

from m365broker.errors import GraphError
from m365broker.graph import GraphClient

BASE = "/graph/v1.0"


@pytest.fixture
def graph(stub, config):
    return GraphClient("access-token-1", base_url=config.graph_base_url, timeout=5)


def test_me_sends_the_bearer_token(stub, graph):
    stub.route("GET", f"{BASE}/me", lambda q, b: (200, {
        "displayName": "Ada Lovelace", "mail": "ada@contoso.onmicrosoft.com"}))

    profile = graph.me()

    assert profile["displayName"] == "Ada Lovelace"


def test_list_messages_requests_newest_first_with_a_bounded_page(stub, graph):
    stub.route("GET", f"{BASE}/me/mailFolders/inbox/messages",
               lambda q, b: (200, {"value": [{"id": "1", "subject": "Hello"}]}))

    messages = graph.list_messages(top=5)

    assert messages[0]["subject"] == "Hello"
    _, _, query, _ = stub.requests_to("GET", f"{BASE}/me/mailFolders/inbox/messages")[0]
    assert query["$top"] == "5"
    assert query["$orderby"] == "receivedDateTime desc"
    assert "bodyPreview" in query["$select"]


def test_list_messages_clamps_an_absurd_page_size(stub, graph):
    """Graph rejects $top above 1000; clamp before it becomes an API error."""
    stub.route("GET", f"{BASE}/me/mailFolders/inbox/messages", lambda q, b: (200, {"value": []}))

    graph.list_messages(top=100000)

    _, _, query, _ = stub.requests_to("GET", f"{BASE}/me/mailFolders/inbox/messages")[0]
    assert query["$top"] == "100"


def test_list_messages_tolerates_an_empty_mailbox(stub, graph):
    stub.route("GET", f"{BASE}/me/mailFolders/inbox/messages", lambda q, b: (200, {}))
    assert graph.list_messages() == []


def test_send_mail_builds_the_graph_payload(stub, graph):
    stub.route("POST", f"{BASE}/me/sendMail", lambda q, b: (202, None))

    graph.send_mail(to="someone@example.com", subject="Subject", body="Body text")

    _, _, _, body = stub.requests_to("POST", f"{BASE}/me/sendMail")[0]
    message = body["message"]
    assert message["subject"] == "Subject"
    assert message["body"] == {"contentType": "Text", "content": "Body text"}
    assert message["toRecipients"] == [{"emailAddress": {"address": "someone@example.com"}}]
    assert body["saveToSentItems"] is True


def test_send_mail_accepts_multiple_recipients_and_cc(stub, graph):
    stub.route("POST", f"{BASE}/me/sendMail", lambda q, b: (202, None))

    graph.send_mail(to=["a@example.com", "b@example.com"], cc="c@example.com",
                    subject="S", body="<b>hi</b>", html=True)

    _, _, _, body = stub.requests_to("POST", f"{BASE}/me/sendMail")[0]
    assert len(body["message"]["toRecipients"]) == 2
    assert body["message"]["ccRecipients"][0]["emailAddress"]["address"] == "c@example.com"
    assert body["message"]["body"]["contentType"] == "HTML"


def test_send_mail_omits_cc_when_not_given(stub, graph):
    stub.route("POST", f"{BASE}/me/sendMail", lambda q, b: (202, None))
    graph.send_mail(to="a@example.com", subject="S", body="B")
    _, _, _, body = stub.requests_to("POST", f"{BASE}/me/sendMail")[0]
    assert "ccRecipients" not in body["message"]


def test_missing_scope_is_reported_with_the_graph_message(stub, graph):
    """The most likely real-world failure: admin consent not granted."""
    stub.route("GET", f"{BASE}/me/mailFolders/inbox/messages", lambda q, b: (403, {
        "error": {"code": "ErrorAccessDenied",
                  "message": "Access is denied. Check credentials and try again."}}))

    with pytest.raises(GraphError) as excinfo:
        graph.list_messages()

    assert excinfo.value.status == 403
    assert excinfo.value.detail == "ErrorAccessDenied"
    assert "Access is denied" in str(excinfo.value)


def test_expired_token_surfaces_as_401(stub, graph):
    stub.route("GET", f"{BASE}/me", lambda q, b: (401, {
        "error": {"code": "InvalidAuthenticationToken", "message": "Access token has expired."}}))

    with pytest.raises(GraphError) as excinfo:
        graph.me()

    assert excinfo.value.status == 401


def test_token_provider_is_called_per_request(stub, config):
    """Proves the client picks up a silently refreshed token automatically."""
    stub.route("GET", f"{BASE}/me", lambda q, b: (200, {"displayName": "Ada"}))
    calls = []

    def provider():
        calls.append(1)
        return f"token-{len(calls)}"

    client = GraphClient(provider, base_url=config.graph_base_url, timeout=5)
    client.me()
    client.me()

    assert len(calls) == 2
