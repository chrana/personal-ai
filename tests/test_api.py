import os
import json
import pytest
from unittest.mock import patch, MagicMock

os.environ["API_KEY"] = "test-key-123"

from fastapi.testclient import TestClient
from app import app


client = TestClient(app)


def mock_embed(text):
    return [0.0] * 1024


class TestHealth:
    def test_root(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_ui(self):
        resp = client.get("/ui")
        assert resp.status_code == 200
        assert "Personal AI" in resp.text


class TestAuth:
    def test_no_key_rejected(self):
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 401

    def test_wrong_key_rejected(self):
        resp = client.post("/chat", json={"message": "hi"}, headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    @patch("app.embed", side_effect=mock_embed)
    def test_valid_key_accepted(self, _):
        with patch("app.bedrock") as mock_bedrock:
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": "Hello!"}]
            }).encode()
            mock_bedrock.invoke_model.return_value = {"body": mock_body}

            resp = client.post(
                "/chat",
                json={"message": "hi", "session_id": "test-auth"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200


class TestChat:
    @patch("app.embed", side_effect=mock_embed)
    def test_returns_response(self, _):
        with patch("app.bedrock") as mock_bedrock:
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": "I can help with that!"}]
            }).encode()
            mock_bedrock.invoke_model.return_value = {"body": mock_body}

            resp = client.post(
                "/chat",
                json={"message": "hello", "session_id": "test-chat"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert "I can help" in resp.json()["response"]

    @patch("app.embed", side_effect=mock_embed)
    def test_stores_history(self, _):
        with patch("app.bedrock") as mock_bedrock:
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": "response"}]
            }).encode()
            mock_bedrock.invoke_model.return_value = {"body": mock_body}

            client.post(
                "/chat",
                json={"message": "test msg", "session_id": "test-history"},
                headers={"Authorization": "Bearer test-key-123"},
            )

        resp = client.get(
            "/history/test-history",
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert any(m["content"] == "test msg" for m in messages)


class TestHistory:
    @patch("app.embed", side_effect=mock_embed)
    def test_clear(self, _):
        with patch("app.bedrock") as mock_bedrock:
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": "hi"}]
            }).encode()
            mock_bedrock.invoke_model.return_value = {"body": mock_body}

            client.post(
                "/chat",
                json={"message": "msg", "session_id": "test-clear"},
                headers={"Authorization": "Bearer test-key-123"},
            )

        resp = client.delete(
            "/history/test-clear",
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 200

        resp = client.get(
            "/history/test-clear",
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.json()["messages"] == []
