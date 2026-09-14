"""Unit tests for the FastAPI web server."""

import pytest
from fastapi.testclient import TestClient

from dojo.server import app


@pytest.fixture
def client():
    return TestClient(app)


def test_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Dojo Zen" in response.text
    assert "Ask Dojo" in response.text


def test_api_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "stats" in data


def test_api_ask(client):
    response = client.post("/api/ask", json={"query": "homework"})
    assert response.status_code == 200
    data = response.json()
    assert "answer" in data
    assert "sources" in data


def test_api_feed(client):
    response = client.get("/api/feed?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_api_briefing(client):
    response = client.get("/api/briefing")
    assert response.status_code == 200
    data = response.json()
    assert "briefing" in data
    assert "action_items" in data["briefing"]
