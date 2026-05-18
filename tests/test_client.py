"""
Unit tests for the Mercatai Python SDK.
Run with: pytest sdk/tests/
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mercatai_agent import MercataiClient
from mercatai_agent.exceptions import AuthError, NotFoundError, RateLimitError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MERCATAI_AGENT_ID", "test-agent-id")
    monkeypatch.setenv("MERCATAI_API_KEY", "test-api-key")
    c = MercataiClient(agent_id="test-agent-id", api_key="test-api-key", base_url="https://test.example.com/api/v1")
    # Pre-populate token to skip login in most tests
    c._access_token = "mock-access-token"
    c._token_expires_at = 9_999_999_999.0
    return c


def _mock_response(data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClientInit:
    def test_requires_agent_id_and_api_key(self):
        with pytest.raises(ValueError):
            MercataiClient()

    def test_accepts_env_vars(self, monkeypatch):
        monkeypatch.setenv("MERCATAI_AGENT_ID", "env-agent")
        monkeypatch.setenv("MERCATAI_API_KEY", "env-key")
        c = MercataiClient()
        assert c.agent_id == "env-agent"

    def test_base_url_strip_trailing_slash(self):
        c = MercataiClient(agent_id="a", api_key="b", base_url="https://example.com/api/v1/")
        assert c.base_url == "https://example.com/api/v1"


class TestListTasks:
    def test_returns_task_list(self, client):
        tasks_data = [{"id": "t1", "title": "Research task", "budget_max_eur": 100}]
        with patch.object(client._session, "get", return_value=_mock_response({"tasks": tasks_data})):
            result = client.list_tasks()
        assert result == tasks_data

    def test_passes_category_param(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"tasks": []})) as mock_get:
            client.list_tasks(category="research", limit=5)
            call_kwargs = mock_get.call_args
            assert call_kwargs.kwargs["params"]["category"] == "research"
            assert call_kwargs.kwargs["params"]["limit"] == 5

    def test_limit_capped_at_100(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"tasks": []})) as mock_get:
            client.list_tasks(limit=999)
            assert mock_get.call_args.kwargs["params"]["limit"] == 100


class TestBid:
    def test_submit_bid(self, client):
        bid_data = {"id": "bid-1", "score": 0.85}
        with patch.object(client._session, "post", return_value=_mock_response(bid_data)):
            result = client.bid(task_id="t1", price_eur=80, estimated_hours=4, proposal="test")
        assert result["id"] == "bid-1"


class TestDeliver:
    def test_deliver_task(self, client):
        task_data = {"id": "t1", "status": "review"}
        with patch.object(client._session, "post", return_value=_mock_response(task_data)):
            result = client.deliver(task_id="t1", result="My result")
        assert result["status"] == "review"


class TestErrorHandling:
    def test_401_raises_auth_error(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"error": "Unauthorized"}, 401)):
            with pytest.raises(AuthError):
                client.list_tasks()

    def test_404_raises_not_found(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"error": "Not found"}, 404)):
            with pytest.raises(NotFoundError):
                client.get_task("nonexistent")

    def test_429_raises_rate_limit_error(self, client):
        with patch.object(client._session, "post", return_value=_mock_response({"error": "Rate limit"}, 429)):
            with pytest.raises(RateLimitError):
                client.bid(task_id="t1", price_eur=10, estimated_hours=1)


class TestToolImports:
    def test_tools_importable(self):
        from mercatai_agent.tools import (
            MercataiDeliverTool,
            MercataiJobFetchTool,
            MercataiSubmitBidTool,
        )
        assert MercataiJobFetchTool is not None
        assert MercataiSubmitBidTool is not None
        assert MercataiDeliverTool is not None
