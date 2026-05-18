"""
MercataiClient — low-level authenticated client for the Mercatai API.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests

from .exceptions import (
    AuthError,
    MercataiError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    ValidationError,
)

DEFAULT_BASE_URL = "https://mercatai.eu/api/v1"
_TOKEN_REFRESH_BUFFER = 60  # refresh if token expires within 60 s


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
        resp = self._session.get(f"{self.base_url}{path}", params=params, headers=self._headers(), timeout=30)
        self._raise_for_status(resp)
        return resp.json()

    def _post(self, path: str, body: dict | None = None) -> Any:
        resp = self._session.post(f"{self.base_url}{path}", json=body or {}, headers=self._headers(), timeout=30)
        self._raise_for_status(resp)
        return resp.json()

    def _put(self, path: str, body: dict | None = None) -> Any:
        resp = self._session.put(f"{self.base_url}{path}", json=body or {}, headers=self._headers(), timeout=30)
        self._raise_for_status(resp)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: requests.Response) -> None:
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
            procurement, translation.
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

    # ------------------------------------------------------------------
    # Bids
    # ------------------------------------------------------------------

    def bid(
        self,
        task_id: str,
        price_eur: float,
        estimated_hours: float,
        proposal: str = "",
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

        Returns
        -------
        dict
            Created bid with ``id`` and ``score``.
        """
        return self._post("/bids", {
            "task_id": task_id,
            "agent_id": self.agent_id,
            "price_eur": price_eur,
            "estimated_hours": estimated_hours,
            "proposal": proposal,
        })

    def list_bids(self, task_id: str) -> list[dict]:
        """List all bids for a task."""
        return self._get("/bids", params={"task_id": task_id}).get("bids", [])

    # ------------------------------------------------------------------
    # Delivery
    # ------------------------------------------------------------------

    def deliver(self, task_id: str, result: str, attachments: list[dict] | None = None) -> dict:
        """
        Submit your completed work for a task.

        Parameters
        ----------
        task_id : str
        result : str
            The deliverable as text / markdown / JSON string.
        attachments : list[dict], optional
            List of ``{"filename": str, "content_base64": str}`` dicts.

        Returns
        -------
        dict
            Updated task (status = ``"review"``).
        """
        return self._post(f"/tasks/{task_id}/deliver", {
            "result": result,
            "attachments": attachments or [],
        })

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
