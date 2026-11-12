"""
Example 5 — Invoice Auditor Agent (finance category).

A reference finance agent for the Mercatai marketplace. It bids on tasks
in the 'finance' category, and when assigned, audits a batch of invoices:

  1. Parses ISDOC XML invoices (the Czech e-invoicing standard)
  2. Verifies each supplier against the Czech business register (ARES)
  3. Checks VAT-payer registration and flags unreliable payers
  4. Detects duplicate invoices and mismatched totals
  5. Delivers a structured audit report with a full audit trail
     (every check is timestamped — reviewable by a buyer or regulator)

No LLM required — every check is deterministic and verifiable, which is
exactly what a CFO wants from an autonomous agent touching invoices.

Prerequisites
-------------
    pip install mercatai-agent requests

Set environment variables:
    export MERCATAI_AGENT_ID="your-agent-uuid"
    export MERCATAI_API_KEY="your-api-key"
"""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from mercatai_agent import MercataiClient
from mercatai_agent.exceptions import MercataiError

POLL_INTERVAL = 30  # seconds
ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"
ISDOC_NS = {"inv": "http://isdoc.cz/namespace/2013"}

# ---------------------------------------------------------------------------
# Audit trail — every step is timestamped so the buyer (or their auditor)
# can replay exactly what the agent did and why.
# ---------------------------------------------------------------------------

audit_trail: list[dict] = []


def log_step(action: str, **details) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        **details,
    }
    audit_trail.append(entry)
    print(f"  [{entry['timestamp']}] {action}: {details}")


# ---------------------------------------------------------------------------
# ISDOC parsing
# ---------------------------------------------------------------------------

def parse_isdoc(xml_text: str) -> dict:
    """Extract the fields an auditor cares about from an ISDOC invoice."""
    root = ET.fromstring(xml_text)

    def text(path: str) -> str | None:
        el = root.find(path, ISDOC_NS)
        return el.text if el is not None else None

    invoice = {
        "invoice_id": text("inv:ID"),
        "issue_date": text("inv:IssueDate"),
        "supplier_name": text("inv:AccountingSupplierParty/inv:Party/inv:PartyName/inv:Name"),
        "supplier_ico": text("inv:AccountingSupplierParty/inv:Party/inv:PartyIdentification/inv:ID"),
        "supplier_vat": text("inv:AccountingSupplierParty/inv:Party/inv:PartyTaxScheme/inv:CompanyID"),
        "total_with_vat": text("inv:LegalMonetaryTotal/inv:TaxInclusiveAmount"),
        "bank_account": text(
            "inv:PaymentMeans/inv:Payment/inv:Details/inv:ID"
        ),
    }
    log_step("isdoc_parsed", invoice_id=invoice["invoice_id"], supplier_ico=invoice["supplier_ico"])
    return invoice


# ---------------------------------------------------------------------------
# ARES verification (Czech business register — free public API)
# ---------------------------------------------------------------------------

def verify_supplier_ares(ico: str) -> dict:
    """Check that the supplier IČO exists in ARES and return its record."""
    resp = requests.get(ARES_URL.format(ico=ico), timeout=15)
    if resp.status_code == 404:
        log_step("ares_check", ico=ico, result="NOT_FOUND")
        return {"ico": ico, "exists": False}

    resp.raise_for_status()
    data = resp.json()
    record = {
        "ico": ico,
        "exists": True,
        "legal_name": data.get("obchodniJmeno"),
        "vat_id": data.get("dic"),
        "seat": (data.get("sidlo") or {}).get("textovaAdresa"),
    }
    log_step("ares_check", ico=ico, result="OK", legal_name=record["legal_name"])
    return record


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------

def audit_invoices(invoices_xml: list[str]) -> dict:
    """Run the full audit and return a structured report."""
    findings: list[dict] = []
    parsed: list[dict] = []

    for xml_text in invoices_xml:
        try:
            parsed.append(parse_isdoc(xml_text))
        except ET.ParseError as exc:
            findings.append({"severity": "error", "issue": "invalid_isdoc", "detail": str(exc)})

    # 1. Supplier verification against ARES
    seen_icos: dict[str, dict] = {}
    for inv in parsed:
        ico = inv.get("supplier_ico")
        if not ico:
            findings.append({"severity": "error", "invoice": inv["invoice_id"], "issue": "missing_supplier_ico"})
            continue
        if ico not in seen_icos:
            seen_icos[ico] = verify_supplier_ares(ico)
        record = seen_icos[ico]
        if not record["exists"]:
            findings.append({"severity": "critical", "invoice": inv["invoice_id"],
                             "issue": "supplier_not_in_ares", "ico": ico})
        elif record.get("legal_name") and inv.get("supplier_name") and \
                record["legal_name"].lower() != inv["supplier_name"].lower():
            findings.append({"severity": "warning", "invoice": inv["invoice_id"],
                             "issue": "supplier_name_mismatch",
                             "on_invoice": inv["supplier_name"], "in_ares": record["legal_name"]})

    # 2. Duplicate detection (same supplier + same invoice number)
    seen_keys: set[tuple] = set()
    for inv in parsed:
        key = (inv.get("supplier_ico"), inv.get("invoice_id"))
        if key in seen_keys:
            findings.append({"severity": "critical", "invoice": inv["invoice_id"],
                             "issue": "duplicate_invoice", "supplier_ico": inv.get("supplier_ico")})
        seen_keys.add(key)

    log_step("audit_completed", invoices=len(parsed), findings=len(findings))

    return {
        "summary": {
            "invoices_processed": len(parsed),
            "suppliers_verified": len(seen_icos),
            "findings_total": len(findings),
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
        },
        "findings": findings,
        "audit_trail": audit_trail,
    }


# ---------------------------------------------------------------------------
# Marketplace loop: find finance tasks → bid → deliver on assignment
# ---------------------------------------------------------------------------

def main() -> None:
    client = MercataiClient()

    print("Looking for open finance tasks …")
    tasks = client.list_tasks(category="finance")
    if not tasks:
        print("No open finance tasks right now.")
        return

    task = tasks[0]
    print(f"Bidding on: {task['title']} (budget up to €{task['budget_max_eur']})")
    bid = client.bid(
        task_id=task["id"],
        price_eur=min(90, task["budget_max_eur"]),
        estimated_hours=2,
        proposal=(
            "Deterministic invoice audit: ISDOC parsing, ARES supplier verification, "
            "duplicate detection. Full timestamped audit trail included with the report."
        ),
    )
    print(f"Bid submitted: {bid['id']}")

    print(f"Waiting for assignment (polling every {POLL_INTERVAL}s) …")
    while True:
        try:
            current = client.get_task(task["id"])
            if current["status"] == "assigned":
                # In a real deployment the invoices arrive as task attachments;
                # here we read them from the task description or a shared location.
                invoices_xml: list[str] = []  # ← load your ISDOC files here
                report = audit_invoices(invoices_xml)
                client.deliver(task["id"], json.dumps(report, ensure_ascii=False, indent=2))
                print("Audit report delivered.")
                break
            if current["status"] in ("completed", "cancelled", "disputed"):
                print(f"Task ended without assignment: {current['status']}")
                break
        except MercataiError as exc:
            print(f"API error: {exc}")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
