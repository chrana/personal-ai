#!/usr/bin/env python3
"""Scheduled bill download and summary. Run via cron.

Schedule (crontab, 9am EST / 14:00 UTC):
  Enbridge:        16th monthly
  Peel Water:      1st of Jan/Apr/Jul/Oct
  Alectra:         25th monthly
  Monthly Summary: 1st monthly (iMessage via SES)

Run manually: python scheduled_bills.py [enbridge|peel-water|alectra|all|monthly-summary]
"""

import asyncio
import json
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.browser import BrowserTool
from tools.orchestrator import read_bill_pdf
from tools.storage import bill_exists
from tools.monitoring import log_browser, logger


async def download_bill(provider: str, property_slug: str, bill_month: str):
    if bill_exists(property_slug, provider, bill_month):
        logger.info(json.dumps({
            "type": "scheduled",
            "action": "skip",
            "provider": provider,
            "property": property_slug,
            "bill_month": bill_month,
            "reason": "already_cached",
        }))
        return {"status": "cached", "property": property_slug, "provider": provider}

    tool = BrowserTool()
    result = await tool.run(action="download_utility_bill", provider=provider, property=property_slug, bill_month=bill_month)

    logger.info(json.dumps({
        "type": "scheduled",
        "action": "download",
        "provider": provider,
        "property": property_slug,
        "bill_month": bill_month,
        "success": result.success,
        "error": result.error if not result.success else None,
    }))

    if result.success:
        summary = read_bill_pdf(property_slug, provider, bill_month, "Quick summary: total amount, due date, usage")
        return {
            "status": "downloaded",
            "property": property_slug,
            "provider": provider,
            "summary": summary.data.get("content", "") if summary.success else "read failed",
        }
    return {"status": "failed", "property": property_slug, "provider": provider, "error": result.error}


async def run_enbridge():
    """Monthly: download current bill for all properties with enbridge."""
    now = datetime.now()
    bill_month = now.strftime("%Y-%m")

    results = []
    for prop in ["windmill", "bellcrest"]:
        result = await download_bill("enbridge", prop, bill_month)
        results.append(result)
        print(f"  {prop}/enbridge/{bill_month}: {result['status']}")

    return results


async def run_peel_water():
    """Quarterly: download current bill for all properties with peel-water."""
    now = datetime.now()
    bill_month = now.strftime("%Y-%m")

    results = []
    for prop in ["windmill", "bellcrest"]:
        result = await download_bill("peel-water", prop, bill_month)
        results.append(result)
        print(f"  {prop}/peel-water/{bill_month}: {result['status']}")

    return results


async def run_alectra():
    """Monthly: download current bill for all properties with alectra."""
    now = datetime.now()
    bill_month = now.strftime("%Y-%m")

    results = []
    for prop in ["windmill", "bellcrest"]:
        result = await download_bill("alectra", prop, bill_month)
        results.append(result)
        print(f"  {prop}/alectra/{bill_month}: {result['status']}")

    return results


def report_cron_metric(provider: str, results: list):
    """Push cron run metrics to CloudWatch."""
    from tools.monitoring import _put_metric
    succeeded = sum(1 for r in results if r["status"] in ("downloaded", "cached"))
    failed = sum(1 for r in results if r["status"] == "failed")
    _put_metric("CronRunSuccess", succeeded, "Count", {"Provider": provider})
    _put_metric("CronRunFailed", failed, "Count", {"Provider": provider})
    _put_metric("CronRunTotal", 1, "Count", {"Provider": provider})


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"[{datetime.now().isoformat()}] Scheduled bill fetch: {provider}")

    if provider in ("enbridge", "all"):
        print("Enbridge:")
        results = await run_enbridge()
        report_cron_metric("enbridge", results)

    if provider in ("peel-water", "all"):
        print("Peel Water:")
        results = await run_peel_water()
        report_cron_metric("peel-water", results)

    if provider in ("alectra", "all"):
        print("Alectra:")
        results = await run_alectra()
        report_cron_metric("alectra", results)

    if provider == "monthly-summary":
        await send_monthly_summary()

    print("Done.")


async def send_monthly_summary():
    """Generate and send monthly bill summary with tenant splits."""
    from tools.billing import split_all_bills
    from tools.notify import send_bill_summary

    now = datetime.now()
    bill_month = now.strftime("%Y-%m")
    result = split_all_bills(bill_month)

    if not result.success or not result.data["bills"]:
        print("  No bills to summarize")
        return

    data = result.data
    lines = [f"Utility Bill Summary — {bill_month}", ""]

    for prop in ["windmill", "bellcrest"]:
        prop_bills = [b for b in data["bills"] if b["property"] == prop]
        if not prop_bills:
            continue
        split_pct = prop_bills[0]["tenant_pct"]
        lines.append(f"{'=' * 30}")
        lines.append(f"{prop.upper()} (tenant pays {split_pct}%)")
        lines.append(f"{'=' * 30}")
        prop_total = 0
        tenant_total = 0
        for b in prop_bills:
            lines.append(f"  {b['provider']:12} ${b['total']:>8.2f}  → tenant: ${b['tenant_amount']:.2f}")
            prop_total += b["total"]
            tenant_total += b["tenant_amount"]
        lines.append(f"  {'TOTAL':12} ${prop_total:>8.2f}  → tenant: ${tenant_total:.2f}")
        lines.append("")

    lines.append(f"{'=' * 30}")
    lines.append(f"YOUR TOTAL (landlord): ${data['landlord_total']:.2f}")
    for prop, amount in data["tenant_totals"].items():
        lines.append(f"  {prop} tenant owes: ${amount:.2f}")

    body = "\n".join(lines)
    subject = f"Bills Summary — {bill_month}"

    print(f"  Sending summary:\n{body}")
    send_bill_summary(subject, body)
    print("  Sent!")


if __name__ == "__main__":
    asyncio.run(main())
