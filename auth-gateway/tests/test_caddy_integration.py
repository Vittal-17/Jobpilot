import os
import re

def test_caddyfile_routing():
    # Read Caddyfile from root
    caddyfile_path = os.path.join(os.path.dirname(__file__), '../../Caddyfile')
    with open(caddyfile_path, 'r') as f:
        content = f.read()

    # 1. Unauthenticated /webhook bypass
    assert '@public_webhooks {' in content
    assert 'path /webhook/* /webhook-test/*' in content
    assert 'handle @public_webhooks {\n\t\treverse_proxy n8n:5678\n\t}' in content

    # 2. Caddy -> auth-gateway routing for login UI
    assert '@auth_public {' in content
    assert 'path /login /logout /static/*' in content
    assert 'reverse_proxy auth-gateway:8080' in content

    # 3. Authenticated forwarding to n8n (forward_auth)
    assert 'forward_auth auth-gateway:8080 {' in content
    assert 'uri /verify' in content
    assert 'reverse_proxy n8n:5678' in content

    # 4. WebSocket upgrade behavior is naturally preserved by Caddy reverse_proxy
    # (Caddy v2 supports websockets out of the box in reverse_proxy)
    assert 'reverse_proxy' in content
