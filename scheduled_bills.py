#!/usr/bin/env python3
"""Scheduled bill download and summary. Run via cron.

Schedule (crontab, 9am EST / 14:00 UTC):
  Enbridge:   16th monthly
  Peel Water: 1st of Jan/Apr/Jul/Oct
  Alectra:    25th monthly

Run manually: python scheduled_bills.py [enbridge|peel-water|alectra|all]
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

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
