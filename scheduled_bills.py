#!/usr/bin/env python3
"""Scheduled bill download and summary. Run via cron."""

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


async def main():
    provider = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"[{datetime.now().isoformat()}] Scheduled bill fetch: {provider}")

    if provider in ("enbridge", "all"):
        print("Enbridge:")
        await run_enbridge()

    if provider in ("peel-water", "all"):
        print("Peel Water:")
        await run_peel_water()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
