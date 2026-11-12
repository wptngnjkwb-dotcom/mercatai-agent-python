"""
Unit tests for the Mercatai Python SDK.
Run with: pytest sdk/tests/
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from mercatai_agent import MercataiClient
from mercatai_agent.exceptions import AuthError, NotFoundError, RateLimitError
from mercatai_agent.finance import FinancialAgentWrapper


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

    def test_sends_canonical_bid_fields_and_optional_sample(self, client):
        with patch.object(client._session, "post", return_value=_mock_response({"id": "bid-1"})) as post:
            client.bid("t1", 8, 2, "Approach", sample_preview="Example")
        assert post.call_args.kwargs["json"] == {
            "task_id": "t1",
            "price_eur": 8,
            "delivery_hours": 2,
            "approach_summary": "Approach",
            "sample_preview": "Example",
        }


class TestAgentRequester:
    def test_create_task_uses_registered_identity_and_idempotency_header(self, client):
        response = {"id": "task-1", "human_approval_required": True}
        with patch.object(client._session, "post", return_value=_mock_response(response)) as post:
            result = client.create_task(
                title="Review an API",
                description="Inspect the API contract and return a short report.",
                category="code_review",
                budget_min_eur=20,
                budget_max_eur=30,
                deadline_hours=24,
                bidding_window_hours=6,
                required_capabilities=["api_review"],
                required_languages=["en"],
                idempotency_key="sdk-task-request-0001",
            )
        assert result == response
        assert post.call_args.args[0].endswith("/tasks")
        assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "sdk-task-request-0001"
        assert post.call_args.kwargs["json"] == {
            "title": "Review an API",
            "description": "Inspect the API contract and return a short report.",
            "category": "code_review",
            "required_capabilities": ["api_review"],
            "required_languages": ["en"],
            "budget_min_eur": 20,
            "budget_max_eur": 30,
            "deadline_hours": 24,
            "bidding_window_hours": 6,
        }
        assert "buyer_email" not in post.call_args.kwargs["json"]

    @pytest.mark.parametrize("key", ["too-short", "x" * 201, "not allowed spaces"])
    def test_create_task_rejects_unsafe_idempotency_key_before_request(self, client, key):
        with patch.object(client._session, "post") as post:
            with pytest.raises(ValueError, match="idempotency_key"):
                client.create_task(
                    title="A task",
                    description="A sufficiently detailed task description.",
                    budget_max_eur=3,
                    deadline_hours=24,
                    idempotency_key=key,
                )
        post.assert_not_called()

    def test_create_direct_offer_sends_complete_terms_without_human_identity(self, client):
        response = {"direct_offer_id": "offer-1", "human_approval_required": True}
        with patch.object(client._session, "post", return_value=_mock_response(response)) as post:
            result = client.create_direct_offer(
                "provider/with spaces",
                title="Analyze a dataset",
                scope="Analyze the supplied dataset and document the findings.",
                category="data_analysis",
                price_eur=25,
                delivery_hours=12,
                deliverables=["Summary", "Clean CSV"],
                required_inputs=["Source CSV"],
                output_formats=["markdown", "csv"],
                listing_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="sdk-direct-offer-0001",
            )
        assert result == response
        assert post.call_args.args[0].endswith("/agents/provider%2Fwith%20spaces/direct-offers")
        assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "sdk-direct-offer-0001"
        body = post.call_args.kwargs["json"]
        assert body["deliverables"] == ["Summary", "Clean CSV"]
        assert body["required_inputs"] == ["Source CSV"]
        assert body["listing_id"] == "11111111-1111-4111-8111-111111111111"
        assert "buyer_email" not in body and "org_name" not in body

    def test_direct_offer_inbox_and_actions_use_private_agent_routes(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"direct_offers": [{"id": "o1"}]})) as get:
            assert client.list_direct_offers() == [{"id": "o1"}]
        assert get.call_args.args[0].endswith("/agents/me/direct-offers")

        with patch.object(client._session, "post", return_value=_mock_response({"status": "awaiting_funding"})) as post:
            client.accept_direct_offer("offer/id", "revision-1")
        assert post.call_args.args[0].endswith("/direct-offers/offer%2Fid/accept")
        assert post.call_args.kwargs["json"] == {"revision_id": "revision-1"}

    def test_counter_decline_withdraw_and_messages_match_the_api(self, client):
        terms = dict(
            title="Counter terms",
            scope="A detailed scope for the counter proposal.",
            category="research",
            price_eur=9,
            delivery_hours=8,
            deliverables=["Report"],
            output_formats=["markdown"],
        )
        with patch.object(client._session, "post", return_value=_mock_response({"ok": True})) as post:
            client.counter_direct_offer("o1", **terms)
            client.decline_direct_offer("o1")
            client.withdraw_direct_offer("o1")
            client.send_direct_offer_message("o1", "Please use public sources only.")
        calls = post.call_args_list
        assert calls[0].args[0].endswith("/direct-offers/o1/counter")
        assert calls[0].kwargs["json"]["required_inputs"] == []
        assert calls[1].args[0].endswith("/direct-offers/o1/decline")
        assert calls[2].args[0].endswith("/direct-offers/o1/withdraw")
        assert calls[3].kwargs["json"] == {"message": "Please use public sources only."}

        with patch.object(client._session, "get", return_value=_mock_response({"messages": [{"message": "ok"}]})):
            assert client.list_direct_offer_messages("o1") == [{"message": "ok"}]


class TestAutonomousPaymentPreparation:
    def test_reads_policy_and_proposes_with_stable_key(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"billing_ready": False})) as get:
            assert client.get_spending_policy() == {"billing_ready": False}
        assert get.call_args.args[0].endswith("/agents/me/spending")

        with patch.object(client._session, "post", return_value=_mock_response({"proposal_id": "p1"}, 201)) as post:
            assert client.propose_payment("task/id", idempotency_key="sdk-payment-proposal-0001") == {"proposal_id": "p1"}
        assert post.call_args.args[0].endswith("/tasks/task%2Fid/payment-proposals")
        assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "sdk-payment-proposal-0001"

    def test_execute_requires_explicit_confirmation_before_network(self, client):
        with patch.object(client._session, "post") as post:
            with pytest.raises(ValueError, match="real charge"):
                client.execute_payment("proposal-1")
        post.assert_not_called()

    def test_execute_with_confirmation_calls_the_api(self, client):
        with patch.object(client._session, "post", return_value=_mock_response({"status": "funded"})) as post:
            result = client.execute_payment("proposal/id", confirm=True)
        assert result["status"] == "funded"
        assert post.call_args.args[0].endswith("/payment-proposals/proposal%2Fid/execute")


class TestDeliver:
    def test_deliver_task(self, client):
        task_data = {"id": "t1", "status": "review"}
        with patch.object(client._session, "post", return_value=_mock_response(task_data)):
            result = client.deliver(task_id="t1", result="My result")
        assert result["status"] == "review"


class TestDeliverContract:
    """POST /tasks/{id}/deliver reads ``delivery_note`` (and nothing else).

    The older test above only checked the response, so the SDK could send a
    field the API ignores and still stay green. These tests pin the request.
    """

    def _deliver(self, client, *args, **kwargs):
        with patch.object(client._session, "post", return_value=_mock_response({"id": "t1", "status": "review"})) as post:
            result = client.deliver(*args, **kwargs)
        return result, post

    def test_sends_delivery_note_to_the_deliver_endpoint(self, client):
        result, post = self._deliver(client, "t1", "My result")
        assert result["status"] == "review"
        assert post.call_args.args[0].endswith("/tasks/t1/deliver")
        assert post.call_args.kwargs["json"] == {"delivery_note": "My result"}

    def test_accepts_delivery_note_as_keyword(self, client):
        _, post = self._deliver(client, task_id="t1", delivery_note="Done")
        assert post.call_args.kwargs["json"] == {"delivery_note": "Done"}

    def test_result_keyword_still_works_but_is_deprecated(self, client):
        with pytest.warns(DeprecationWarning, match="delivery_note"):
            _, post = self._deliver(client, task_id="t1", result="Old style")
        assert post.call_args.kwargs["json"] == {"delivery_note": "Old style"}

    def test_both_names_at_once_is_rejected(self, client):
        with pytest.raises(ValueError, match="delivery_note"):
            client.deliver("t1", "a", result="b")

    def test_a_missing_note_is_rejected_before_any_request(self, client):
        with patch.object(client._session, "post") as post:
            with pytest.raises(ValueError, match="delivery_note"):
                client.deliver("t1")
        post.assert_not_called()

    def test_attachments_are_not_silently_claimed_as_delivered(self, client):
        with pytest.warns(UserWarning, match="attachments"):
            _, post = self._deliver(client, "t1", "Done", attachments=[{"filename": "a.txt", "content_base64": "eA=="}])
        assert post.call_args.kwargs["json"] == {"delivery_note": "Done"}


class TestAuditedWrapperDeliver:
    def test_forwards_the_note_with_the_audit_trail(self):
        inner = MagicMock()
        inner.deliver.return_value = {"status": "review"}
        wrapper = FinancialAgentWrapper(inner)
        wrapper.deliver("t1", json.dumps({"summary": "ok"}))
        sent = inner.deliver.call_args.args[1]
        assert json.loads(sent)["summary"] == "ok"
        assert json.loads(sent)["audit_trail"][-1]["action"] == "deliver"

    def test_result_keyword_still_works_but_is_deprecated(self):
        inner = MagicMock()
        wrapper = FinancialAgentWrapper(inner)
        with pytest.warns(DeprecationWarning, match="delivery_note"):
            wrapper.deliver("t1", result="plain text")
        assert "plain text" in inner.deliver.call_args.args[1]


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


# ---------------------------------------------------------------------------
# Redirects: the address without www answers every request with a 307 to the address with it
# ---------------------------------------------------------------------------

class TestRedirects:
    def _redirect(self, status=307, location="https://www.mercatai.eu/api/v1/tasks?status=open&token=SECRET"):
        resp = _mock_response({}, status)
        resp.headers = {"Location": location}
        resp.json.side_effect = ValueError("not json")
        resp.text = "Temporary Redirect"
        return resp

    def test_the_default_address_is_the_one_with_www(self):
        from mercatai_agent import __version__
        from mercatai_agent.client import DEFAULT_BASE_URL

        assert DEFAULT_BASE_URL == "https://www.mercatai.eu/api/v1"
        assert MercataiClient(agent_id="a", api_key="b").base_url == "https://www.mercatai.eu/api/v1"
        assert __version__ == "0.2.0"

    @pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
    @pytest.mark.parametrize("verb", ["get", "post", "put"])
    def test_a_redirect_is_an_error_that_says_where_it_points_and_is_never_followed(self, client, verb, status):
        from mercatai_agent.exceptions import RedirectError

        with patch.object(client._session, verb, return_value=self._redirect(status)) as call:
            with pytest.raises(RedirectError) as caught:
                {"get": lambda: client._get("/tasks"), "post": lambda: client._post("/bids", {}), "put": lambda: client._put("/x", {})}[verb]()
        assert caught.value.status_code == status
        assert caught.value.location == "https://www.mercatai.eu/api/v1/tasks"   # no query string: it can carry a token
        assert "SECRET" not in str(caught.value) and "token" not in str(caught.value).lower().replace("redirects", "")
        assert "base_url" in str(caught.value)
        assert call.call_count == 1                                                # one attempt, no second request to the new address
        assert call.call_args.kwargs["allow_redirects"] is False

    def test_every_request_the_client_makes_forbids_redirects_including_the_login(self, monkeypatch):
        c = MercataiClient(agent_id="a", api_key="b", base_url="https://test.example.com/api/v1")
        login = _mock_response({"access_token": "t"})
        with patch.object(c._session, "post", return_value=login) as post, patch.object(c._session, "get", return_value=_mock_response({"tasks": []})) as get:
            c.list_tasks()
        assert post.call_args.kwargs["allow_redirects"] is False
        assert get.call_args.kwargs["allow_redirects"] is False

    def test_a_redirect_of_the_login_never_sends_the_api_key_anywhere_else(self):
        from mercatai_agent.exceptions import RedirectError

        c = MercataiClient(agent_id="a", api_key="the-key", base_url="https://mercatai.eu/api/v1")
        with patch.object(c._session, "post", return_value=self._redirect(307, "https://www.mercatai.eu/api/v1/auth/login")) as post:
            with pytest.raises(RedirectError):
                c._ensure_token()
        assert post.call_count == 1
        assert "the-key" not in str(post.return_value.headers)

    def test_a_redirect_without_a_usable_location_is_still_a_clear_error(self, client):
        from mercatai_agent.exceptions import RedirectError

        for headers in ({}, {"Location": ""}, {"Location": None}):
            resp = self._redirect(302)
            resp.headers = headers
            with patch.object(client._session, "get", return_value=resp):
                with pytest.raises(RedirectError) as caught:
                    client._get("/tasks")
            assert caught.value.location is None and "redirect" in str(caught.value)

    def test_it_is_a_mercatai_error_and_is_exported(self):
        import mercatai_agent
        from mercatai_agent.exceptions import MercataiError, RedirectError

        assert issubclass(RedirectError, MercataiError)
        assert mercatai_agent.RedirectError is RedirectError

    def test_an_ordinary_answer_is_unchanged(self, client):
        with patch.object(client._session, "get", return_value=_mock_response({"tasks": []})):
            assert client.list_tasks() == []
