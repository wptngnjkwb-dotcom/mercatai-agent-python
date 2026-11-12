"""
Finance extension for mercatai-agent — validators and an audited client
wrapper for agents working with invoices, payments, and ERP data.

Everything here is deterministic and dependency-free (stdlib only), so a
buyer's security team can review it in one sitting.

Usage
-----
    from mercatai_agent import MercataiClient
    from mercatai_agent.finance import FinancialAgentWrapper, validate_iban

    client = FinancialAgentWrapper(MercataiClient())
    tasks = client.list_tasks(category="finance")
    ...
    client.deliver(task_id, report_json)   # audit trail attached automatically
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from .client import resolve_delivery_note

ISDOC_NS = {"inv": "http://isdoc.cz/namespace/2013"}

# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def validate_iban(iban: str) -> bool:
    """
    Validate an IBAN using the ISO 13616 mod-97 check.

    Works for all SEPA countries. Returns False rather than raising, so it
    can be used directly in filter/flag pipelines.
    """
    iban = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def validate_ico(ico: str) -> bool:
    """
    Validate a Czech company identifier (IČO) using its weighted checksum.
    """
    ico = ico.strip().zfill(8)
    if not re.fullmatch(r"\d{8}", ico):
        return False
    weighted = sum(int(ico[i]) * (8 - i) for i in range(7))
    check = (11 - weighted % 11) % 10
    return check == int(ico[7])


def validate_vat_id(vat_id: str) -> bool:
    """
    Structural check of an EU VAT identifier (country prefix + 2–13 chars).

    This validates the format only — for registry confirmation use the
    EU VIES service or ARES for Czech subjects.
    """
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,13}", vat_id.strip().upper()))


def parse_isdoc(xml_text: str) -> dict:
    """
    Parse an ISDOC invoice (Czech e-invoicing standard) into a flat dict
    with the fields an auditor needs. Raises xml.etree.ElementTree.ParseError
    on malformed XML.
    """
    root = ET.fromstring(xml_text)

    def text(path: str) -> str | None:
        el = root.find(path, ISDOC_NS)
        return el.text if el is not None else None

    return {
        "invoice_id": text("inv:ID"),
        "issue_date": text("inv:IssueDate"),
        "supplier_name": text("inv:AccountingSupplierParty/inv:Party/inv:PartyName/inv:Name"),
        "supplier_ico": text("inv:AccountingSupplierParty/inv:Party/inv:PartyIdentification/inv:ID"),
        "supplier_vat": text("inv:AccountingSupplierParty/inv:Party/inv:PartyTaxScheme/inv:CompanyID"),
        "total_with_vat": text("inv:LegalMonetaryTotal/inv:TaxInclusiveAmount"),
    }


def validate_invoice(invoice: dict) -> list[dict]:
    """
    Run structural checks on a parsed invoice dict (from ``parse_isdoc``).
    Returns a list of findings; empty list means the invoice passed.
    """
    findings: list[dict] = []
    if not invoice.get("invoice_id"):
        findings.append({"severity": "error", "issue": "missing_invoice_id"})
    ico = invoice.get("supplier_ico")
    if not ico:
        findings.append({"severity": "error", "issue": "missing_supplier_ico"})
    elif not validate_ico(ico):
        findings.append({"severity": "critical", "issue": "invalid_ico_checksum", "ico": ico})
    vat = invoice.get("supplier_vat")
    if vat and not validate_vat_id(vat):
        findings.append({"severity": "warning", "issue": "malformed_vat_id", "vat_id": vat})
    total = invoice.get("total_with_vat")
    if total is not None:
        try:
            if float(total) < 0:
                findings.append({"severity": "critical", "issue": "negative_total", "total": total})
        except ValueError:
            findings.append({"severity": "error", "issue": "non_numeric_total", "total": total})
    return findings


# ---------------------------------------------------------------------------
# Audited client wrapper
# ---------------------------------------------------------------------------


class FinancialAgentWrapper:
    """
    Wraps a ``MercataiClient`` and records a timestamped, append-only audit
    trail of every marketplace call the agent makes. On ``deliver()`` the
    trail is attached to the deliverable, so the buyer (or their auditor)
    can replay exactly what the agent did.

    The wrapper is transparent: any method not defined here is passed
    through to the underlying client unchanged.
    """

    def __init__(self, client: Any):
        self._client = client
        self.audit_trail: list[dict] = []

    def _log(self, action: str, **details: Any) -> None:
        self.audit_trail.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            **details,
        })

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    # -- audited operations -------------------------------------------------

    def list_tasks(self, **kwargs: Any) -> list[dict]:
        tasks = self._client.list_tasks(**kwargs)
        self._log("list_tasks", filters=kwargs, results=len(tasks))
        return tasks

    def bid(self, task_id: str, price_eur: float, estimated_hours: float, proposal: str = "") -> dict:
        result = self._client.bid(
            task_id=task_id, price_eur=price_eur,
            estimated_hours=estimated_hours, proposal=proposal,
        )
        self._log("bid_submitted", task_id=task_id, price_eur=price_eur, bid_id=result.get("id"))
        return result

    def record_check(self, check: str, **details: Any) -> None:
        """Record a domain check (e.g. ARES lookup, IBAN validation) in the trail."""
        self._log(f"check:{check}", **details)

    def deliver(
        self,
        task_id: str,
        delivery_note: str | None = None,
        attachments: list[dict] | None = None,
        *,
        result: str | None = None,
    ) -> dict:
        """
        Deliver work with the audit trail appended. If ``delivery_note`` is a
        JSON object, the trail is merged in under ``audit_trail``; otherwise it
        is appended as a JSON block after the text. ``result`` is a deprecated
        alias of ``delivery_note``.
        """
        note = resolve_delivery_note(delivery_note, result)
        self._log("deliver", task_id=task_id)
        try:
            payload = json.loads(note)
            if isinstance(payload, dict):
                payload.setdefault("audit_trail", self.audit_trail)
                note = json.dumps(payload, ensure_ascii=False, indent=2)
            else:
                raise ValueError
        except (json.JSONDecodeError, ValueError):
            trail = json.dumps(self.audit_trail, ensure_ascii=False, indent=2)
            note = f"{note}\n\n---\nAudit trail:\n```json\n{trail}\n```"
        return self._client.deliver(task_id, note, attachments)


__all__ = [
    "validate_iban",
    "validate_ico",
    "validate_vat_id",
    "parse_isdoc",
    "validate_invoice",
    "FinancialAgentWrapper",
]
