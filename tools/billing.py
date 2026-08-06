import json
import boto3
from tools.base import ToolResult
from tools.storage import get_bills_for_usage_month, get_bill_metadata
from config import PROPERTIES

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"


def get_cost_split(property_slug: str) -> dict:
    prop = PROPERTIES.get(property_slug, {})
    return prop.get("cost_split", {"landlord_pct": 100, "tenant_pct": 0})


def split_from_metadata(meta: dict) -> dict:
    split = get_cost_split(meta["property"])
    total = meta["total_amount"]
    landlord_amount = round(total * split["landlord_pct"] / 100, 2)
    tenant_amount = round(total * split["tenant_pct"] / 100, 2)
    return {
        "property": meta["property"],
        "provider": meta["provider"],
        "usage_month": meta["usage_month"],
        "billing_period_start": meta.get("billing_period_start"),
        "billing_period_end": meta.get("billing_period_end"),
        "due_date": meta.get("due_date"),
        "total": total,
        "landlord_pct": split["landlord_pct"],
        "tenant_pct": split["tenant_pct"],
        "landlord_amount": landlord_amount,
        "tenant_amount": tenant_amount,
        "s3_key": meta.get("s3_key"),
    }


def split_bill(property_slug: str, provider: str, bill_month: str) -> ToolResult:
    """Split a specific bill. bill_month here refers to the file key."""
    meta = get_bill_metadata(property_slug, provider, bill_month)
    if not meta:
        return ToolResult(success=False, error=f"No metadata for {property_slug}/{provider}/{bill_month}. Run extraction first.")

    return ToolResult(success=True, data=split_from_metadata(meta))


def split_by_usage_month(usage_month: str) -> ToolResult:
    """Get all bills attributed to a usage month with splits."""
    bills = get_bills_for_usage_month(usage_month)

    # Deduplicate — same property/provider/usage_month might have multiple files
    seen = set()
    unique_bills = []
    for b in bills:
        key = (b["property"], b["provider"], b["usage_month"])
        if key not in seen:
            seen.add(key)
            unique_bills.append(b)

    results = [split_from_metadata(b) for b in unique_bills]

    landlord_total = sum(r["landlord_amount"] for r in results)
    tenant_totals = {}
    for r in results:
        prop = r["property"]
        tenant_totals[prop] = tenant_totals.get(prop, 0) + r["tenant_amount"]

    return ToolResult(success=True, data={
        "usage_month": usage_month,
        "bills": results,
        "landlord_total": round(landlord_total, 2),
        "tenant_totals": {k: round(v, 2) for k, v in tenant_totals.items()},
    })


# Keep for backwards compatibility with orchestrator
def split_all_bills(bill_month: str) -> ToolResult:
    return split_by_usage_month(bill_month)
