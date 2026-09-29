import pytest
from fastapi.testclient import TestClient
from app.main import app

def test_fastapi_login_rate_limiting_ip():
    with TestClient(app) as client:
        # Client IP spoofing/real parsing check
        headers = {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"}
        for _ in range(10):
            res = client.post("/v1/auth/login", json={"email": "nonexistent@example.com", "password": "b"}, headers=headers)
            assert res.status_code == 401

        # 11th attempt is blocked for the same IP
        res = client.post("/v1/auth/login", json={"email": "another@example.com", "password": "b"}, headers=headers)
        assert res.status_code == 429

        # Different real client IP works
        headers2 = {"X-Forwarded-For": "10.0.0.1, 10.0.0.3"}
        res = client.post("/v1/auth/login", json={"email": "yetanother@example.com", "password": "b"}, headers=headers2)
        assert res.status_code == 401

def test_fastapi_login_rate_limiting_email():
    with TestClient(app) as client:
        # Same email, different IPs (distributed attack)
        for i in range(10):
            headers = {"X-Forwarded-For": f"10.0.0.{i+10}"}
            res = client.post("/v1/auth/login", json={"email": "target@example.com", "password": "b"}, headers=headers)
            assert res.status_code == 401

        # 11th attempt for the same email is blocked
        headers_new = {"X-Forwarded-For": "10.0.0.99"}
        res = client.post("/v1/auth/login", json={"email": "target@example.com", "password": "b"}, headers=headers_new)
        assert res.status_code == 429
