import pytest
from fastapi.testclient import TestClient
from datetime import datetime
from sqlalchemy.orm import Session
from app.db.models.user import User
from app.db.models.user_search import UserSearch
from app.main import app
from app.db.database import get_db

@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()

@pytest.fixture
def auth_client(client, db_session):
    client.post("/v1/auth/register", json={"email": "searchesuser@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "searchesuser@example.com", "password": "SecurePassword123!"})
    token = login_res.headers.get("set-cookie").split(";")[0].split("=")[1].strip()
    client.cookies.set("session_token", token)
    return client

def test_create_search(auth_client: TestClient):
    response = auth_client.post("/v1/searches", json={"query": "python developer", "location": "remote", "remote_only": True})
    assert response.status_code == 201
    data = response.json()
    assert data["query"] == "python developer"
    assert data["location"] == "remote"
    assert data["remote_only"] is True
    assert data["enabled"] is True
    assert "id" in data

def test_create_search_empty_validation(auth_client: TestClient):
    # Missing both
    response = auth_client.post("/v1/searches", json={"remote_only": True})
    assert response.status_code == 422

    # Empty string both
    response = auth_client.post("/v1/searches", json={"query": "  ", "location": ""})
    assert response.status_code == 422

    # Just location should succeed
    response = auth_client.post("/v1/searches", json={"location": "London"})
    assert response.status_code == 201
    assert response.json()["location"] == "London"

def test_list_searches(auth_client: TestClient):
    auth_client.post("/v1/searches", json={"query": "A"})
    auth_client.post("/v1/searches", json={"query": "B"})

    response = auth_client.get("/v1/searches")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2
    queries = [item["query"] for item in data["items"]]
    assert "A" in queries
    assert "B" in queries

def test_get_search(auth_client: TestClient):
    create_res = auth_client.post("/v1/searches", json={"query": "C"})
    s_id = create_res.json()["id"]

    get_res = auth_client.get(f"/v1/searches/{s_id}")
    assert get_res.status_code == 200
    assert get_res.json()["query"] == "C"

def test_update_search(auth_client: TestClient):
    create_res = auth_client.post("/v1/searches", json={"query": "D", "enabled": True})
    s_id = create_res.json()["id"]

    update_res = auth_client.patch(f"/v1/searches/{s_id}", json={"query": "E", "enabled": False})
    assert update_res.status_code == 200
    data = update_res.json()
    assert data["query"] == "E"
    assert data["enabled"] is False

def test_update_search_validation(auth_client: TestClient):
    create_res = auth_client.post("/v1/searches", json={"query": "F"})
    s_id = create_res.json()["id"]

    update_res = auth_client.patch(f"/v1/searches/{s_id}", json={"query": " ", "location": ""})
    assert update_res.status_code == 422

def test_delete_search(auth_client: TestClient):
    create_res = auth_client.post("/v1/searches", json={"query": "G"})
    s_id = create_res.json()["id"]

    del_res = auth_client.delete(f"/v1/searches/{s_id}")
    assert del_res.status_code == 204

    get_res = auth_client.get(f"/v1/searches/{s_id}")
    assert get_res.status_code == 404

def test_search_isolation(auth_client: TestClient, client: TestClient, db_session: Session):
    # Create search for user A
    create_res = auth_client.post("/v1/searches", json={"query": "H"})
    s_id = create_res.json()["id"]

    # User B
    client.post("/v1/auth/register", json={"email": "searchesuser2@example.com", "password": "SecurePassword123!"})
    login_res = client.post("/v1/auth/login", json={"email": "searchesuser2@example.com", "password": "SecurePassword123!"})
    token = login_res.headers.get("set-cookie").split(";")[0].split("=")[1].strip()
    client.cookies.set("session_token", token)

    # Try to access User A's search as User B
    res = client.get(f"/v1/searches/{s_id}")
    assert res.status_code == 404

    # Try to update User A's search as User B
    res = client.patch(f"/v1/searches/{s_id}", json={"query": "I"})
    assert res.status_code == 404

    # Try to delete User A's search as User B
    res = client.delete(f"/v1/searches/{s_id}")
    assert res.status_code == 404
