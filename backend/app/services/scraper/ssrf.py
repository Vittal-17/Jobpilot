"""Small, synchronous HTTP client with request-local SSRF destination pinning.

The important detail here is that DNS validation and the TCP connect must use
the same address.  Patching ``socket.getaddrinfo`` is process-global (and can
race unrelated requests), so the client instead supplies httpcore with a
network backend that connects to the address selected for this request.  The
URL itself is not rewritten: httpcore therefore retains the original Host
header and TLS SNI name.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


class SSRFViolation(Exception):
    pass


class _PinnedNetworkBackend:
    """httpcore backend which connects to one already-validated IP address."""

    def __init__(self, address: str):
        self.address = str(ipaddress.ip_address(address))

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ):
        # ``host`` is deliberately ignored.  It is the original hostname and
        # is kept as the httpcore origin so TLS still uses that hostname for
        # SNI/certificate verification.  The socket only ever connects to the
        # validated literal address below; no DNS lookup occurs here.
        import httpcore
        from httpcore._backends.sync import SyncStream

        ip = ipaddress.ip_address(self.address)
        family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
        sockaddr = (self.address, port, 0, 0) if family == socket.AF_INET6 else (self.address, port)
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(timeout)
            if local_address:
                local_ip = ipaddress.ip_address(local_address)
                local_sockaddr = (
                    (str(local_ip), 0, 0, 0)
                    if local_ip.version == 6
                    else (str(local_ip), 0)
                )
                sock.bind(local_sockaddr)
            for option in socket_options or ():
                sock.setsockopt(*option)
            # Match httpcore's normal TCP behavior without relying on its DNS
            # resolving backend.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.connect(sockaddr)
        except socket.timeout as exc:
            sock.close()
            raise httpcore.ConnectTimeout(str(exc)) from exc
        except OSError as exc:
            sock.close()
            raise httpcore.ConnectError(str(exc)) from exc
        return SyncStream(sock)

    def connect_unix_socket(self, *args: Any, **kwargs: Any):
        raise RuntimeError("Unix sockets are not supported by SSRFClient")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class _PinnedHTTPTransport(httpx.HTTPTransport):
    """HTTPX transport using a request-local, already validated destination."""

    def __init__(self, address: str):
        # ``trust_env=False`` is also set on the Client.  Keep it here too so
        # this transport cannot accidentally inherit proxy/environment
        # behavior if it is reused independently.
        super().__init__(
            verify=True,
            trust_env=False,
            proxy=None,
            retries=0,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        )
        pool = getattr(self, "_pool", None)
        if pool is None or not hasattr(pool, "_network_backend"):
            # A future incompatible httpx/httpcore pair should fail closed,
            # rather than silently falling back to an unpinned resolver.
            self.close()
            raise SSRFViolation("HTTPX transport cannot provide destination binding")
        pool._network_backend = _PinnedNetworkBackend(address)


class SSRFClient:
    def __init__(
        self,
        max_redirects: int = 5,
        timeout: float = 10.0,
        max_bytes: int = 1 * 1024 * 1024,
    ):
        self.max_redirects = max(0, int(max_redirects))
        self.timeout = float(timeout)
        self.max_bytes = max(1, int(max_bytes))

    def _validate_ip(self, ip_addr: str) -> None:
        try:
            ip = ipaddress.ip_address(ip_addr)
        except (TypeError, ValueError) as exc:
            raise SSRFViolation("Resolved address is not a valid IP address") from exc

        # Keep the explicit metadata check for a useful, stable diagnostic;
        # it is also covered by is_link_local below.
        if ip in {
            ipaddress.ip_address("169.254.169.254"),
            ipaddress.ip_address("::ffff:169.254.169.254"),
        }:
            raise SSRFViolation("Blocked metadata service IP")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise SSRFViolation(f"Blocked private/reserved IP: {ip}")
        # On Python versions where IPv4-mapped IPv6 metadata is not delegated
        # by IPv6Address.is_global, check the embedded IPv4 address explicitly.
        mapped_ip = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or (mapped_ip is not None and not mapped_ip.is_global):
            raise SSRFViolation(f"Blocked non-global IP: {ip}")

    def _resolve_and_validate(self, hostname: str) -> list[str]:
        try:
            addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SSRFViolation(f"DNS resolution failed: {exc}") from exc

        addresses: list[str] = []
        seen: set[str] = set()
        for _family, _type, _proto, _canonname, sockaddr in addrinfo:
            try:
                ip_addr = str(ipaddress.ip_address(sockaddr[0]))
            except (IndexError, TypeError, ValueError) as exc:
                raise SSRFViolation("DNS returned an invalid address") from exc
            self._validate_ip(ip_addr)
            if ip_addr not in seen:
                seen.add(ip_addr)
                addresses.append(ip_addr)
        if not addresses:
            raise SSRFViolation("DNS resolution returned no addresses")
        return addresses

    def _validate_url(self, url: str, *, resolve: bool = True) -> None:
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            # Accessing .port also validates its syntax/range.
            parsed.port
        except ValueError as exc:
            raise SSRFViolation("Malformed URL") from exc
        if parsed.scheme not in ("http", "https"):
            raise SSRFViolation(f"Blocked scheme: {parsed.scheme}")
        if not hostname:
            raise SSRFViolation("Missing hostname")

        try:
            literal = str(ipaddress.ip_address(hostname))
        except ValueError:
            if resolve:
                self._resolve_and_validate(hostname)
        else:
            self._validate_ip(literal)

    def _select_destination(self, hostname: str) -> str:
        try:
            literal = str(ipaddress.ip_address(hostname))
        except ValueError:
            addresses = self._resolve_and_validate(hostname)
            if not isinstance(addresses, (list, tuple)) or not addresses:
                raise SSRFViolation("DNS resolution returned no addresses")
            self._validate_ip(addresses[0])
            return addresses[0]
        self._validate_ip(literal)
        return literal

    @staticmethod
    def _peer_address(response: httpx.Response) -> str:
        extensions = getattr(response, "extensions", None)
        stream = extensions.get("network_stream") if isinstance(extensions, dict) else None
        getter = getattr(stream, "get_extra_info", None)
        if stream is None or not callable(getter):
            raise SSRFViolation("Unable to verify connected peer")
        try:
            peer = getter("server_addr")
            peer_ip = peer[0] if isinstance(peer, (tuple, list)) and peer else None
            actual = str(ipaddress.ip_address(peer_ip)) if peer_ip is not None else None
        except SSRFViolation:
            raise
        except Exception as exc:
            raise SSRFViolation("Unable to verify connected peer") from exc
        if actual is None:
            raise SSRFViolation("Unable to verify connected peer")
        return actual

    def _verify_peer(
        self,
        response: httpx.Response,
        selected_address: str,
    ) -> None:
        actual = self._peer_address(response)
        self._validate_ip(actual)
        selected = str(ipaddress.ip_address(selected_address))
        if actual != selected:
            raise SSRFViolation(
                f"Connected peer {actual} does not match selected destination {selected}"
            )

    def fetch(self, url: str) -> httpx.Response:
        redirect_count = 0
        current_url = str(url)
        deadline = time.monotonic() + self.timeout

        while redirect_count <= self.max_redirects:
            if time.monotonic() >= deadline:
                raise SSRFViolation("Response deadline exceeded")

            # Re-parse, resolve, and validate on every hop.  Never carry the
            # selected address across redirects, even for the same hostname.
            # _select_destination performs the one DNS lookup used for this
            # hop.  Avoid resolving twice, which would reintroduce a DNS
            # race between validation and binding.
            self._validate_url(current_url, resolve=False)
            parsed_current = urlparse(current_url)
            hostname = parsed_current.hostname
            if not hostname:
                raise SSRFViolation("Missing hostname")
            selected_address = self._select_destination(hostname)
            remaining = max(0.001, deadline - time.monotonic())

            transport = _PinnedHTTPTransport(selected_address)
            try:
                with httpx.Client(
                    timeout=remaining,
                    follow_redirects=False,
                    verify=True,
                    trust_env=False,
                    transport=transport,
                ) as client:
                    with client.stream("GET", current_url) as response:
                        # A response without connection metadata is not safe
                        # to consume, even when its DNS result was public.
                        self._verify_peer(response, selected_address)

                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise SSRFViolation("Redirect missing location header")
                            current_url = urljoin(current_url, location)
                            redirect_count += 1
                            continue

                        content_type = response.headers.get("content-type", "").lower()
                        if "text/html" not in content_type and "text/plain" not in content_type:
                            raise SSRFViolation(f"Blocked content type: {content_type}")

                        # Content-Length is the wire length.  iter_bytes is the
                        # decoded stream, so the loop also bounds the body
                        # retained by this client after HTTPX decoding.
                        declared = response.headers.get("content-length")
                        if declared is not None:
                            try:
                                declared_length = int(declared.strip())
                            except (TypeError, ValueError):
                                raise SSRFViolation("Malformed Content-Length")
                            if declared_length < 0 or declared_length > self.max_bytes:
                                raise SSRFViolation(
                                    f"Response too large: exceeded {self.max_bytes} bytes"
                                )

                        content = bytearray()
                        for chunk in response.iter_bytes(chunk_size=8192):
                            if time.monotonic() >= deadline:
                                raise SSRFViolation("Response deadline exceeded")
                            content.extend(chunk)
                            if len(content) > self.max_bytes:
                                raise SSRFViolation(
                                    f"Response too large: exceeded {self.max_bytes} bytes"
                                )

                        headers = dict(response.headers)
                        headers.pop("content-encoding", None)
                        headers.pop("transfer-encoding", None)
                        headers["content-length"] = str(len(content))
                        return httpx.Response(
                            status_code=response.status_code,
                            headers=headers,
                            content=bytes(content),
                            request=response.request,
                            extensions=response.extensions,
                        )
            finally:
                # Client.__exit__ normally closes this, but explicit close
                # also handles a mocked Client/transport and keeps no pooled
                # connection alive across request-local bindings.
                transport.close()

        raise SSRFViolation(f"Too many redirects (max {self.max_redirects})")
