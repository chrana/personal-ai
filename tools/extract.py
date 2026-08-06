import json
import re
import boto3
import base64
import io
from pdf2image import convert_from_path
from tools.storage import download_bill, save_bill_metadata, bill_exists

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"

EXTRACT_PROMPT = """Extract billing metadata from this utility bill. Return ONLY a JSON object with:
{
  "billing_period_start": "YYYY-MM-DD",
  "billing_period_end": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD",
  "total_amount": <number>
}
No other text, no markdown fences."""


def extract_and_save_metadata(property_slug: str, provider: str, bill_month: str) -> dict:
    if not bill_exists(property_slug, provider, bill_month):
        return {}

    local_path = download_bill(property_slug, provider, bill_month)

    images = convert_from_path(local_path, dpi=150)
    image_contents = []
    for img in images[:2]:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        image_contents.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    response = bedrock.invoke_model(
        modelId=MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": image_contents + [{"type": "text", "text": EXTRACT_PROMPT}]}],
        }),
    )

    result = json.loads(response["body"].read())
    text = result["content"][0]["text"].strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text)

    try:
        metadata = json.loads(text)
    except json.JSONDecodeError:
        return {}

    # Determine usage_month from billing_period_end
    # The usage month is the month the billing period mostly covers
    # Use the end date's month (or month before if end is in first few days)
    end_date = metadata.get("billing_period_end", "")
    if end_date:
        parts = end_date.split("-")
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        if day <= 5:
            # Billing ended in early part of month — usage is previous month
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        metadata["usage_month"] = f"{year:04d}-{month:02d}"

    metadata["property"] = property_slug
    metadata["provider"] = provider
    metadata["bill_month"] = bill_month
    metadata["s3_key"] = f"bills/{property_slug}/{provider}/{bill_month}.pdf"

    save_bill_metadata(property_slug, provider, bill_month, metadata)
    return metadata
