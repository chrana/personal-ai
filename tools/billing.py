import json
import boto3
from tools.base import ToolResult
from tools.secrets import get_secret
from tools.orchestrator import read_bill_pdf

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"


def get_cost_split(property_slug: str) -> dict:
    config = get_secret("personal-ai/config")
    prop = config["properties"].get(property_slug, {})
    return prop.get("cost_split", {"landlord_pct": 100, "tenant_pct": 0})


def split_bill(property_slug: str, provider: str, bill_month: str) -> ToolResult:
    result = read_bill_pdf(property_slug, provider, bill_month, "What is the total amount due? Return ONLY the numeric dollar amount, e.g. 329.71")

    if not result.success:
        return result

    content = result.data["content"]

    try:
        response = bedrock.invoke_model(
            modelId=MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 64,
                "messages": [{"role": "user", "content": f"Extract ONLY the numeric total amount from this text. Return just the number, no $ sign or text.\n\n{content}"}],
            }),
        )
        amount_text = json.loads(response["body"].read())["content"][0]["text"].strip()
        total = float(amount_text.replace(",", ""))
    except (ValueError, KeyError):
        return ToolResult(success=False, error=f"Could not parse bill amount from: {content}")

    split = get_cost_split(property_slug)
    landlord_amount = round(total * split["landlord_pct"] / 100, 2)
    tenant_amount = round(total * split["tenant_pct"] / 100, 2)

    return ToolResult(success=True, data={
        "property": property_slug,
        "provider": provider,
        "bill_month": bill_month,
        "total": total,
        "landlord_pct": split["landlord_pct"],
        "tenant_pct": split["tenant_pct"],
        "landlord_amount": landlord_amount,
        "tenant_amount": tenant_amount,
    })


def split_all_bills(bill_month: str) -> ToolResult:
    config = get_secret("personal-ai/config")
    results = []

    for slug, prop in config["properties"].items():
        split = prop.get("cost_split")
        if not split:
            continue
        for provider in prop["utilities"]:
            bill_result = split_bill(slug, provider, bill_month)
            if bill_result.success:
                results.append(bill_result.data)

    landlord_total = sum(r["landlord_amount"] for r in results)
    tenant_totals = {}
    for r in results:
        key = r["property"]
        tenant_totals[key] = tenant_totals.get(key, 0) + r["tenant_amount"]

    return ToolResult(success=True, data={
        "bill_month": bill_month,
        "bills": results,
        "landlord_total": round(landlord_total, 2),
        "tenant_totals": {k: round(v, 2) for k, v in tenant_totals.items()},
    })
