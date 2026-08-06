import os
import boto3
from botocore.exceptions import ClientError
from tools.base import Tool, ToolResult

BUCKET = "personal-ai-bills-310603388222"
LOCAL_STORAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")

s3 = boto3.client("s3", region_name="us-east-1")


def s3_key(property_slug: str, provider: str, bill_month: str) -> str:
    return f"bills/{property_slug}/{provider}/{bill_month}.pdf"


def bill_exists(property_slug: str, provider: str, bill_month: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=s3_key(property_slug, provider, bill_month))
        return True
    except ClientError:
        return False


def download_bill(property_slug: str, provider: str, bill_month: str) -> str:
    os.makedirs(LOCAL_STORAGE, exist_ok=True)
    local_path = os.path.join(LOCAL_STORAGE, f"{property_slug}_{provider}_{bill_month}.pdf")
    s3.download_file(BUCKET, s3_key(property_slug, provider, bill_month), local_path)
    return local_path


def upload_bill(local_path: str, property_slug: str, provider: str, bill_month: str):
    s3.upload_file(local_path, BUCKET, s3_key(property_slug, provider, bill_month))


def list_bills(property_slug: str = "", provider: str = "") -> list:
    prefix = "bills/"
    if property_slug:
        prefix += f"{property_slug}/"
        if provider:
            prefix += f"{provider}/"

    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    bills = []
    for obj in resp.get("Contents", []):
        parts = obj["Key"].replace("bills/", "").replace(".pdf", "").split("/")
        if len(parts) == 3:
            bills.append({
                "property": parts[0],
                "provider": parts[1],
                "bill_month": parts[2],
            })
    return bills


class StorageTool(Tool):
    name = "storage"
    description = "Manages bill storage in S3"
    permissions = ["s3"]

    async def run(self, action: str, **kwargs) -> ToolResult:
        actions = {
            "check_bill": self._check_bill,
            "get_bill": self._get_bill,
        }
        if action not in actions:
            return ToolResult(success=False, error=f"Unknown action: {action}")
        return await actions[action](**kwargs)

    async def _check_bill(self, property_slug: str, provider: str, bill_month: str) -> ToolResult:
        exists = bill_exists(property_slug, provider, bill_month)
        return ToolResult(success=True, data={"exists": exists})

    async def _get_bill(self, property_slug: str, provider: str, bill_month: str) -> ToolResult:
        if not bill_exists(property_slug, provider, bill_month):
            return ToolResult(success=False, error="Bill not found in storage")
        local_path = download_bill(property_slug, provider, bill_month)
        return ToolResult(success=True, data={"filepath": local_path})
