"""P0 regressions for proxy-resistant SSRF and notification acknowledgement."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.scraper.ssrf import SSRFClient, SSRFViolation, _PinnedNetworkBackend


@pytest.mark.parametrize(
    "url",
    [
        "http://100.64.0.0/",
        "http://100.127.255.255/",
        "http://[::ffff:100.64.0.0]/",
        "http://[::ffff:100.127.255.255]/",
    ],
)
def test_ssrf_literal_urls_reject_non_global_shared_address_boundaries(url):
    with pytest.raises(SSRFViolation, match="Blocked"):
        SSRFClient()._validate_url(url, resolve=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://93.184.216.34/",
        "http://100.63.255.255/",
        "http://100.128.0.0/",
        "http://[::ffff:8.8.8.8]/",
    ],
)
def test_ssrf_literal_urls_accept_global_and_mapped_global_addresses(url):
    SSRFClient()._validate_url(url, resolve=False)


@pytest.mark.parametrize(
    "answer",
    [
        "100.64.0.0",
        "100.127.255.255",
        "::ffff:100.64.0.0",
        "::ffff:100.127.255.255",
    ],
)
def test_ssrf_dns_answers_reject_non_global_shared_address_boundaries(monkeypatch, answer):
    monkeypatch.setattr(
        "app.services.scraper.ssrf.socket.getaddrinfo",
        lambda *_args: [(socket.AF_UNSPEC, socket.SOCK_STREAM, 0, "", (answer, 0))],
    )

    with pytest.raises(SSRFViolation, match="Blocked"):
        SSRFClient()._validate_url("https://shared.example/", resolve=True)


@pytest.mark.parametrize("answer", ["93.184.216.34", "100.63.255.255", "100.128.0.0", "::ffff:8.8.8.8"])
def test_ssrf_dns_answers_accept_global_and_mapped_global_addresses(monkeypatch, answer):
    monkeypatch.setattr(
        "app.services.scraper.ssrf.socket.getaddrinfo",
        lambda *_args: [(socket.AF_UNSPEC, socket.SOCK_STREAM, 0, "", (answer, 0))],
    )

    assert SSRFClient()._resolve_and_validate("public.example") == [answer]


def test_ssrf_metadata_diagnostic_is_preserved_for_mapped_metadata_address():
    with pytest.raises(SSRFViolation, match="Blocked metadata service IP"):
        SSRFClient()._validate_ip("::ffff:169.254.169.254")


def test_ssrf_fetch_disables_environment_proxies_and_redirect_following(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://169.254.169.254:8080")
    response = httpx.Response(
        200,
        headers={"content-type": "text/plain"},
        content=b"ok",
        request=httpx.Request("GET", "http://example.com"),
    )
    peer_stream = MagicMock()
    peer_stream.get_extra_info.return_value = ("93.184.216.34", 80)
    response.extensions = {"network_stream": peer_stream}

    fake_client = MagicMock()
    fake_client.__enter__.return_value.stream.return_value.__enter__.return_value = response
    client = SSRFClient()
    with patch.object(client, "_select_destination", return_value="93.184.216.34"), patch(
        "app.services.scraper.ssrf._PinnedHTTPTransport"
    ) as transport, patch("app.services.scraper.ssrf.httpx.Client", return_value=fake_client) as factory:
        fetched = client.fetch("http://example.com")

    assert fetched.content == b"ok"
    kwargs = factory.call_args.kwargs
    assert kwargs["trust_env"] is False
    assert kwargs["follow_redirects"] is False
    assert kwargs["transport"] is transport.return_value


def test_ssrf_pinned_connect_does_not_reresolve_hostname():
    backend = _PinnedNetworkBackend("93.184.216.34")
    with patch("app.services.scraper.ssrf.socket.socket") as socket_factory, patch(
        "app.services.scraper.ssrf.socket.getaddrinfo", side_effect=AssertionError("DNS rebinding")
    ) as resolver:
        backend.connect_tcp("rebinding.example", 443, timeout=10)
    resolver.assert_not_called()
    socket_factory.return_value.connect.assert_called_once_with(("93.184.216.34", 443))


@pytest.mark.parametrize("peer", [None, (), "93.184.216.34", ("not-an-ip", 80)])
def test_ssrf_malformed_peer_metadata_is_rejected_before_body_read(peer):
    response = MagicMock()
    response.extensions = {"network_stream": MagicMock()}
    response.extensions["network_stream"].get_extra_info.return_value = peer
    client = SSRFClient()
    with patch.object(client, "_select_destination", return_value="93.184.216.34"), patch(
        "app.services.scraper.ssrf.httpx.Client.stream"
    ) as stream:
        stream.return_value.__enter__.return_value = response
        with pytest.raises(SSRFViolation, match="Unable to verify connected peer"):
            client.fetch("https://example.com/jobs/1234")
    response.iter_bytes.assert_not_called()


def test_ssrf_redirect_rebinding_reresolves_and_blocks_before_next_connection():
    response = MagicMock()
    response.is_redirect = True
    response.headers = {"location": "/private-after-redirect"}
    response.extensions = {"network_stream": MagicMock()}
    response.extensions["network_stream"].get_extra_info.return_value = ("93.184.216.34", 443)
    client = SSRFClient()
    with patch.object(
        client, "_resolve_and_validate", side_effect=[["93.184.216.34"], ["169.254.169.254"]]
    ) as resolver, patch("app.services.scraper.ssrf.httpx.Client.stream") as stream:
        stream.return_value.__enter__.return_value = response
        with pytest.raises(SSRFViolation, match="Blocked metadata service IP"):
            client.fetch("https://example.com/jobs/1234")
    assert resolver.call_count == 2
    assert stream.call_count == 1
    response.iter_bytes.assert_not_called()


def _notification_nodes():
    workflow = json.loads(
        (Path(__file__).resolve().parents[2] / "JP___Notifications.json").read_text(encoding="utf-8")
    )[0]
    return {node["name"]: node for node in workflow["nodes"]}


def _evaluate_ack(item):
    expression = _notification_nodes()["Acknowledge Delivery"]["parameters"]["jsonBody"].strip()
    assert expression.startswith("={{") and expression.endswith("}}")
    expression = expression[3:-2].strip()
    # Use exactly the deployed expression, not a Python reimplementation of its
    # guard.  Node is already a project test dependency; no network is used.
    script = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const claim = {user_id: 7, delivery_id: 'p0-send'};
const $ = name => ({item: {json: name === 'Send Telegram' ? input.item : claim}});
try {
    const value = new Function('$', 'return (' + input.expression + ')')($);
    process.stdout.write(JSON.stringify({ack: JSON.parse(value)}));
} catch (error) {
    process.stdout.write(JSON.stringify({blocked: true}));
}
"""
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps({"item": item, "expression": expression}),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "item",
    [
        {"ok": False, "result": {"message_id": 123}},
        {},
        {"ok": True},
        {"ok": True, "result": {}},
        {"ok": True, "result": {"message_id": 0}},
        {"ok": True, "result": {"message_id": -1}},
        {"ok": True, "result": {"message_id": True}},
        {"ok": True, "result": {"message_id": "not-a-number"}},
        {"statusCode": 504, "body": {"ok": True, "result": {"message_id": 123}}},
        {"error": {"message": "ETIMEDOUT"}},
        {"body": "malformed response"},
        [],
    ],
    ids=["ok_false", "empty", "missing_result", "missing_id", "zero_id", "negative_id", "bool_id",
         "malformed_id", "timeout_status", "timeout_error", "malformed_body", "array_body"],
)
def test_telegram_false_malformed_and_timeout_cannot_authorize_ack(item):
    assert _evaluate_ack(item) == {"blocked": True}


@pytest.mark.parametrize(
    "item",
    [
        {"statusCode": 200, "body": {"ok": True, "result": {"message_id": 123}}},
        {"statusCode": 200, "body": {"ok": True, "result": {"message_id": "123"}}},
    ],
)
def test_telegram_valid_message_id_authorizes_only_claim_owner_ack(item):
    assert _evaluate_ack(item) == {"ack": {"user_id": 7, "delivery_id": "p0-send"}}
