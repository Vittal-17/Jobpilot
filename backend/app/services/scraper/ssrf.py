import socket
import ipaddress
from urllib.parse import urlparse
import httpx

class SSRFViolation(Exception):
    pass

class SSRFClient:
    def __init__(self, max_redirects: int = 5, timeout: float = 10.0, max_bytes: int = 1 * 1024 * 1024):
        self.max_redirects = max_redirects
        self.timeout = timeout
        self.max_bytes = max_bytes

    def _validate_ip(self, ip_addr: str) -> None:
        ip = ipaddress.ip_address(ip_addr)
        if str(ip) == '169.254.169.254' or (isinstance(ip, ipaddress.IPv6Address) and str(ip) == '::ffff:169.254.169.254'):
            raise SSRFViolation("Blocked metadata service IP")
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise SSRFViolation(f"Blocked private/reserved IP: {ip}")

    def _resolve_and_validate(self, hostname: str) -> None:
        try:
            # Check both IPv4 and IPv6
            addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, type, proto, canonname, sockaddr in addrinfo:
                ip_addr = sockaddr[0]
                self._validate_ip(ip_addr)
        except socket.gaierror as e:
            raise SSRFViolation(f"DNS resolution failed: {e}")
        return ip_addr

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise SSRFViolation(f"Blocked scheme: {parsed.scheme}")
        if not parsed.hostname:
            raise SSRFViolation("Missing hostname")

        # Check if the hostname itself is an IP address
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            self._validate_ip(str(ip))
        except ValueError:
            # It's a hostname, resolve it
            self._resolve_and_validate(parsed.hostname)

    def fetch(self, url: str) -> httpx.Response:
        redirect_count = 0
        current_url = str(url)

        with httpx.Client(timeout=self.timeout, follow_redirects=False, verify=True) as client:
            while redirect_count <= self.max_redirects:
                self._validate_url(current_url)

                with client.stream("GET", current_url) as response:
                    # Post-connection check to protect against DNS rebinding
                    stream = response.extensions.get("network_stream")
                    if stream:
                        server_addr = stream.get_extra_info("server_addr")
                        if server_addr:
                            self._validate_ip(server_addr[0])

                    if response.is_redirect:
                        redirect_count += 1
                        current_url = response.headers.get("location")
                        if not current_url:
                            raise SSRFViolation("Redirect missing location header")
                        parsed = urlparse(current_url)
                        if not parsed.netloc:
                            base_parsed = urlparse(str(response.url))
                            current_url = f"{base_parsed.scheme}://{base_parsed.netloc}{current_url}"
                        continue

                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        raise SSRFViolation(f"Blocked content type: {content_type}")

                    # Read the response in chunks to enforce max_bytes strictly
                    content = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        content.extend(chunk)
                        if len(content) > self.max_bytes:
                            raise SSRFViolation(f"Response too large: exceeded {self.max_bytes} bytes")

                    headers = dict(response.headers)
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)

                    final_response = httpx.Response(
                        status_code=response.status_code,
                        headers=headers,
                        content=bytes(content),
                        request=response.request,
                        extensions=response.extensions
                    )

                return final_response

            raise SSRFViolation(f"Too many redirects (max {self.max_redirects})")
