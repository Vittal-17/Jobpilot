import pytest
from unittest.mock import patch, MagicMock
import httpx
from app.services.scraper.ssrf import SSRFClient, SSRFViolation

def test_ssrf_rejects_private_ips():
    client = SSRFClient()
    with pytest.raises(SSRFViolation, match="Blocked private/reserved IP"):
        client._resolve_and_validate("127.0.0.1")
    with pytest.raises(SSRFViolation, match="Blocked private/reserved IP"):
        client._resolve_and_validate("192.168.1.1")
    with pytest.raises(SSRFViolation, match="Blocked private/reserved IP"):
        client._resolve_and_validate("10.0.0.1")

def test_ssrf_rejects_metadata():
    client = SSRFClient()
    with pytest.raises(SSRFViolation, match="Blocked metadata service IP"):
        client._resolve_and_validate("169.254.169.254")

def test_ssrf_accepts_public():
    client = SSRFClient()
    # Should not raise exception
    ip = client._resolve_and_validate("google.com")
    assert ip is not None

def test_ssrf_rejects_non_http():
    client = SSRFClient()
    with pytest.raises(SSRFViolation, match="Blocked scheme: file"):
        client._validate_url("file:///etc/passwd")

def test_fetch_rejects_oversized_response():
    client = SSRFClient(max_bytes=10) # 10 bytes limit

    with patch("httpx.Client.stream") as mock_stream:
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "text/html"}
        mock_response.iter_bytes.return_value = [b"1234567890123"] # 13 bytes
        # mock extensions
        mock_response.extensions = {"network_stream": MagicMock(get_extra_info=lambda x: ("93.184.216.34", 443))}

        # mock context manager
        mock_stream.return_value.__enter__.return_value = mock_response

        with patch.object(client, "_resolve_and_validate"):
            with pytest.raises(SSRFViolation, match="Response too large"):
                client.fetch("http://safe.com")

def test_fetch_rejects_redirect_to_private():
    client = SSRFClient()

    with patch("httpx.Client.stream") as mock_stream:
        # First response is a redirect
        mock_resp1 = MagicMock()
        mock_resp1.is_redirect = True
        mock_resp1.headers = {"location": "http://169.254.169.254/latest/meta-data/"}
        mock_resp1.url = httpx.URL("http://safe.com")
        mock_resp1.extensions = {"network_stream": MagicMock(get_extra_info=lambda x: ("93.184.216.34", 443))}

        mock_stream.return_value.__enter__.return_value = mock_resp1

        with patch.object(client, "_resolve_and_validate") as mock_resolve:
            # We want _resolve_and_validate to actually run to catch the bad IP
            mock_resolve.side_effect = SSRFClient()._resolve_and_validate
            with pytest.raises(SSRFViolation, match="Blocked metadata service IP"):
                client.fetch("http://safe.com")

def test_fetch_catches_dns_rebinding_post_connect():
    client = SSRFClient()

    with patch("httpx.Client.stream") as mock_stream:
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "text/html"}
        mock_response.iter_bytes.return_value = [b"ok"]

        # Simulate network_stream resolving to metadata IP AFTER initial DNS passed
        mock_stream_obj = MagicMock()
        mock_stream_obj.get_extra_info.return_value = ("169.254.169.254", 80)
        mock_response.extensions = {"network_stream": mock_stream_obj}

        mock_stream.return_value.__enter__.return_value = mock_response

        with patch.object(client, "_resolve_and_validate"):
            # Initial DNS mock passes, but post-connect check should fail
            with pytest.raises(SSRFViolation, match="Blocked metadata service IP"):
                client.fetch("http://safe.com")

def test_fetch_rejects_invalid_content_type():
    client = SSRFClient()

    with patch("httpx.Client.stream") as mock_stream:
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.headers = {"content-type": "application/json"}
        mock_response.iter_bytes.return_value = [b"{}"]
        mock_response.extensions = {"network_stream": MagicMock(get_extra_info=lambda x: ("93.184.216.34", 443))}

        mock_stream.return_value.__enter__.return_value = mock_response

        with patch.object(client, "_resolve_and_validate"):
            with pytest.raises(SSRFViolation, match="Blocked content type: application/json"):
                client.fetch("http://safe.com")

def test_fetch_returns_usable_body():
    client = SSRFClient()

    with patch("httpx.Client.stream") as mock_stream:
        mock_response = MagicMock()
        mock_response.is_redirect = False
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html", "content-encoding": "gzip", "content-length": "100"}
        mock_response.iter_bytes.return_value = [b"<html>", b"<body>Valid</body></html>"]
        mock_response.request = MagicMock()
        mock_response.extensions = {"network_stream": MagicMock(get_extra_info=lambda x: ("93.184.216.34", 443))}

        mock_stream.return_value.__enter__.return_value = mock_response

        with patch.object(client, "_resolve_and_validate"):
            response = client.fetch("http://safe.com")

            # Assert the body is fully readable without StreamConsumed errors
            assert response.content == b"<html><body>Valid</body></html>"
            assert response.text == "<html><body>Valid</body></html>"
            # Assert headers were cleaned and recalculated
            assert "content-encoding" not in response.headers
            assert response.headers.get("content-length") == str(len(response.content))
            assert response.status_code == 200
