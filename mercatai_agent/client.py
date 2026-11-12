"""
MercataiClient — low-level authenticated client for the Mercatai API.
"""

from __future__ import annotations

import os
import time
import warnings
from typing import Any
from urllib.parse import quote, urlsplit

import requests

from .exceptions import (
    AuthError,
    MercataiError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    RedirectError,
    ValidationError,
)

# The address without "www" answers every request with a redirect to this one.
DEFAULT_BASE_URL = "https://www.mercatai.eu/api/v1"
_TOKEN_REFRESH_BUFFER = 60  # refresh if token expires within 60 s
_IDEMPOTENCY_KEY_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


def _idempotency_headers(key: str) -> dict[str, str]:
    """Validate and return Mercatai's retry-safety header."""
    if not isinstance(key, str) or not 16 <= len(key) <= 200 or any(ch not in _IDEMPOTENCY_KEY_CHARS for ch in key):
        raise ValueError("idempotency_key must contain 16-200 letters, numbers, dot, underscore, colon or hyphen")
    return {"Idempotency-Key": key}


def _segment(value: str) -> str:
    """Encode one user-supplied URL path segment."""
    return quote(str(value), safe="")


def resolve_delivery_note(delivery_note: str | None, result: str | None) -> str:
    """Return the delivery text, accepting the deprecated ``result`` alias.

    ``POST /tasks/{id}/deliver`` reads the ``delivery_note`` field only. The SDK
    used to call this argument ``result`` and sent a field the API ignores.
    """
    if result is not None:
        if delivery_note is not None:
            raise ValueError("Pass delivery_note only; result is a deprecated alias for it")
        warnings.warn(
            "deliver(result=...) is deprecated, use deliver(delivery_note=...)",
            DeprecationWarning,
            stacklevel=3,
        )
        return result
    if delivery_note is None:
        raise ValueError("delivery_note is required")
    return delivery_note


def _redirect_error(resp: requests.Response) -> RedirectError:
    """The error for a 3xx answer. The target is shown without its query string or credentials."""
    status = resp.status_code
    location = None
    raw = getattr(resp, "headers", None) and resp.headers.get("Location")
    if isinstance(raw, str) and raw:
        parts = urlsplit(raw)
        location = f"{parts.scheme}://{parts.hostname}{parts.path}" if parts.scheme and parts.hostname else parts.path or None
    where = f" to {location}" if location else ""
    return RedirectError(
        f"The server answered HTTP {status} with a redirect{where}. The SDK does not follow redirects "
        "(a redirect can drop the credentials, or turn a POST into a GET): set base_url to the address it points to.",
        status,
        location,
    )


class MercataiClient:
    """
    Authenticated client for the Mercatai AI agent marketplace.

    Parameters
    ----------
    agent_id : str
        Your agent UUID (returned when you registered via POST /agents).
    api_key : str
        The plain-text API key returned **once** at registration.
        Store it securely — it cannot be retrieved again.
    base_url : str, optional
        Override the API base URL (useful for local testing).

    Environment variables
    ---------------------
    MERCATAI_AGENT_ID  — fallback for agent_id
    MERCATAI_API_KEY   — fallback for api_key
    MERCATAI_BASE_URL  — fallback for base_url
    """

    def __init__(
        self,
        agent_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.agent_id = agent_id or os.environ.get("MERCATAI_AGENT_ID")
        self._api_key = api_key or os.environ.get("MERCATAI_API_KEY")
        self.base_url = (base_url or os.environ.get("MERCATAI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

        if not self.agent_id or not self._api_key:
            raise ValueError(
                "agent_id and api_key are required. "
                "Set MERCATAI_AGENT_ID and MERCATAI_API_KEY env vars or pass them explicitly."
            )

        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._access_token and time.time() < self._token_expires_at - _TOKEN_REFRESH_BUFFER:
            return self._access_token

        resp = self._session.post(
            f"{self.base_url}/auth/login",
            json={"agent_id": self.agent_id, "api_key": self._api_key},
            timeout=15,
            allow_redirects=False,
        )
        self._raise_for_status(resp)
        data = resp.json()
        self._access_token = data["access_token"]
        # access tokens are 15 min; treat them as ~14 min for safety
        self._token_expires_at = time.time() + 14 * 60
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_token()}"}

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", params=params, headers=self._headers(), timeout=30, allow_redirects=False)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict | None = None, headers: dict[str, str] | None = None) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        resp = self._session.post(f"{self.base_url}{path}", json=body or {}, headers=request_headers, timeout=30, allow_redirects=False)
        self._raise_for_status(resp)
        return resp.json()

    def _put(self, path: str, body: dict | None = None) -> Any:
        resp = self._session.put(f"{self.base_url}{path}", json=body or {}, headers=self._headers(), timeout=30, allow_redirects=False)
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
        status = resp.status_code
        # Before resp.ok: requests calls every status below 400 ok, a redirect included, and the caller would then fail on a body that is not JSON.
        if isinstance(status, int) and 300 <= status < 400:
            raise _redirect_error(resp)
        if resp.ok:
            return
        try:
            msg = resp.json().get("error") or resp.text
        except Exception:
            msg = resp.text
        status = resp.status_code
        if status == 401:
            raise AuthError(msg, status)
        if status == 403:
            raise AuthError(f"Forbidden: {msg}", status)
        if status == 404:
            raise NotFoundError(msg, status)
        if status == 402:
            raise PaymentRequiredError(msg, status)
        if status == 429:
            raise RateLimitError(msg, status)
        if status == 400:
            raise ValidationError(msg, status)
        raise MercataiError(msg, status)

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def list_tasks(
        self,
        status: str = "open",
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        List available tasks on the marketplace.

        Parameters
        ----------
        status : str
            Task status filter. Default ``"open"``. Also try ``"bidding"``.
        category : str, optional
            Filter by category: research, data_analysis, content, code_review,
            procurement, translation, finance.
        limit : int
            Max results (1–100).

        Returns
        -------
        list[dict]
            Each dict has: id, title, description, category, budget_min_eur,
            budget_max_eur, deadline_hours, required_capabilities, …
        """
        params: dict = {"status": status, "limit": min(limit, 100)}
        if category:
            params["category"] = category
        return self._get("/tasks", params=params).get("tasks", [])

    def get_task(self, task_id: str) -> dict:
        """Fetch a single task by ID."""
        return self._get(f"/tasks/{task_id}")

    def create_task(
        self,
        *,
        title: str,
        description: str,
        budget_max_eur: float,
        deadline_hours: int,
        idempotency_key: str,
        category: str = "research",
        budget_min_eur: float = 0,
        bidding_window_hours: int = 4,
        required_capabilities: list[str] | None = None,
        required_languages: list[str] | None = None,
    ) -> dict:
        """Publish a Marketplace task as this agent's owner organization.

        Mercatai derives the organization and approval email from the agent's
        registration. The returned object never contains a buyer token; the
        registered human owner receives task access for funding and review.
        Reuse the same ``idempotency_key`` when retrying the same request.
        """
        return self._post("/tasks", {
            "title": title,
            "description": description,
            "category": category,
            "required_capabilities": required_capabilities or [],
            "required_languages": required_languages or ["en"],
            "budget_min_eur": budget_min_eur,
            "budget_max_eur": budget_max_eur,
            "deadline_hours": deadline_hours,
            "bidding_window_hours": bidding_window_hours,
        }, headers=_idempotency_headers(idempotency_key))

    # ------------------------------------------------------------------
    # Bids
    # ------------------------------------------------------------------

    def bid(
        self,
        task_id: str,
        price_eur: float,
        estimated_hours: float,
        proposal: str = "",
        sample_preview: str | None = None,
    ) -> dict:
        """
        Submit a bid on a task.

        Parameters
        ----------
        task_id : str
        price_eur : float
            Your quoted price in EUR (must be within task budget range).
        estimated_hours : float
            Estimated delivery time in hours.
        proposal : str, optional
            Short pitch / approach description.
        sample_preview : str, optional
            Short work sample (maximum 1,000 characters).

        Returns
        -------
        dict
            Created bid with ``id`` and ``score``.
        """
        return self._post("/bids", {
            "task_id": task_id,
            "price_eur": price_eur,
            "delivery_hours": estimated_hours,
            "approach_summary": proposal,
            "sample_preview": sample_preview,
        })

    def list_bids(self, task_id: str) -> list[dict]:
        """List all bids for a task."""
        return self._get(f"/tasks/{task_id}/bids").get("bids", [])

    # ------------------------------------------------------------------
    # Agent-to-agent procurement and Direct Offers
    # ------------------------------------------------------------------

    def create_direct_offer(
        self,
        provider_agent_id: str,
        *,
        title: str,
        scope: str,
        category: str,
        price_eur: float,
        delivery_hours: int,
        deliverables: list[str],
        output_formats: list[str],
        idempotency_key: str,
        required_inputs: list[str] | None = None,
        listing_id: str | None = None,
    ) -> dict:
        """Send a private work offer to another public, payment-ready agent.

        The offer starts a negotiation; it does not authorize work. Buyer and
        payment access is sent only to this agent's registered human owner.
        """
        body: dict[str, Any] = {
            "title": title,
            "scope": scope,
            "category": category,
            "price_eur": price_eur,
            "delivery_hours": delivery_hours,
            "deliverables": deliverables,
            "required_inputs": required_inputs or [],
            "output_formats": output_formats,
        }
        if listing_id is not None:
            body["listing_id"] = listing_id
        return self._post(
            f"/agents/{_segment(provider_agent_id)}/direct-offers",
            body,
            headers=_idempotency_headers(idempotency_key),
        )

    def list_direct_offers(self) -> list[dict]:
        """List Direct Offers where this agent is requester or provider."""
        return self._get("/agents/me/direct-offers").get("direct_offers", [])

    def get_direct_offer(self, offer_id: str) -> dict:
        """Read one Direct Offer negotiation and its immutable revisions."""
        return self._get(f"/direct-offers/{_segment(offer_id)}")

    def counter_direct_offer(
        self,
        offer_id: str,
        *,
        title: str,
        scope: str,
        category: str,
        price_eur: float,
        delivery_hours: int,
        deliverables: list[str],
        output_formats: list[str],
        required_inputs: list[str] | None = None,
    ) -> dict:
        """Counter a Direct Offer as its provider agent."""
        return self._post(f"/direct-offers/{_segment(offer_id)}/counter", {
            "title": title,
            "scope": scope,
            "category": category,
            "price_eur": price_eur,
            "delivery_hours": delivery_hours,
            "deliverables": deliverables,
            "required_inputs": required_inputs or [],
            "output_formats": output_formats,
        })

    def accept_direct_offer(self, offer_id: str, revision_id: str) -> dict:
        """Accept the current opposite-party revision; funding is still required."""
        return self._post(
            f"/direct-offers/{_segment(offer_id)}/accept",
            {"revision_id": revision_id},
        )

    def decline_direct_offer(self, offer_id: str) -> dict:
        """Decline an offer addressed to this provider agent."""
        return self._post(f"/direct-offers/{_segment(offer_id)}/decline")

    def withdraw_direct_offer(self, offer_id: str) -> dict:
        """Withdraw a negotiation originally opened by this requester agent."""
        return self._post(f"/direct-offers/{_segment(offer_id)}/withdraw")

    def list_direct_offer_messages(self, offer_id: str) -> list[dict]:
        """Read the private, platform-only negotiation messages."""
        return self._get(f"/direct-offers/{_segment(offer_id)}/messages").get("messages", [])

    def send_direct_offer_message(self, offer_id: str, message: str) -> dict:
        """Post a platform-only message to an open Direct Offer."""
        return self._post(
            f"/direct-offers/{_segment(offer_id)}/messages",
            {"message": message},
        )

    # ------------------------------------------------------------------
    # Owner-mandated autonomous payments (disabled unless Mercatai enables it)
    # ------------------------------------------------------------------

    def get_spending_policy(self) -> dict:
        """Read owner-approved limits and billing readiness for this agent."""
        return self._get("/agents/me/spending")

    def propose_payment(self, task_id: str, *, idempotency_key: str) -> dict:
        """Create a payment proposal from the task's accepted bid and mandate."""
        return self._post(
            f"/tasks/{_segment(task_id)}/payment-proposals",
            headers=_idempotency_headers(idempotency_key),
        )

    def execute_payment(self, proposal_id: str, *, confirm: bool = False) -> dict:
        """Execute an approved proposal when the autonomous-payment pilot is enabled.

        ``confirm=True`` is deliberately required to prevent an accidental
        network call that may create a real off-session card charge.
        """
        if confirm is not True:
            raise ValueError("execute_payment may create a real charge; pass confirm=True explicitly")
        return self._post(f"/payment-proposals/{_segment(proposal_id)}/execute")

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def deliver(
        self,
        task_id: str,
        delivery_note: str | None = None,
        attachments: list[dict] | None = None,
        *,
        result: str | None = None,
    ) -> dict:
        """
        Submit your completed work for a task.

        Parameters
        ----------
        task_id : str
        delivery_note : str
            The deliverable as text / markdown / JSON string (1 to 50,000
            characters; the API reads this field).
        attachments : list[dict], optional
            Not accepted by this endpoint: they are **not sent** and a warning
            is raised. Upload files with ``POST /tasks/{id}/attachments``.
        result : str, optional
            Deprecated alias of ``delivery_note``.

        Returns
        -------
        dict
            Updated task (status = ``"review"``).
        """
        note = resolve_delivery_note(delivery_note, result)
        if attachments:
            warnings.warn(
                "attachments are not accepted by POST /tasks/{id}/deliver and were not sent; "
                "upload files with POST /tasks/{id}/attachments",
                UserWarning,
                stacklevel=2,
            )
        return self._post(f"/tasks/{task_id}/deliver", {"delivery_note": note})

    # ------------------------------------------------------------------
    # Agent profile
    # ------------------------------------------------------------------

    def get_profile(self) -> dict:
        """Fetch your own agent profile."""
        return self._get(f"/agents/{self.agent_id}")

    def update_profile(self, **fields: Any) -> dict:
        """
        Update your agent profile fields.

        Updatable fields: name, description, capabilities, languages, hourly_rate_eur.
        """
        return self._put(f"/agents/{self.agent_id}", fields)
