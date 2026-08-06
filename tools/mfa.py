import re
import time
import email
import boto3
from datetime import datetime, timezone, timedelta

s3 = boto3.client("s3", region_name="us-east-1")
BUCKET = "personal-ai-bills-310603388222"
PREFIX = "mfa-emails/"

MFA_PATTERNS = [
    r"\b(\d{6})\b",
    r"verification code[:\s]*(\d{4,8})",
    r"security code[:\s]*(\d{4,8})",
    r"one.time.*?(\d{6})",
    r"passcode[:\s]*(\d{4,8})",
]


def get_mfa_code(sender_filter: str = "", max_wait: int = 60, poll_interval: int = 5) -> str:
    """Poll S3 for a recent MFA email and extract the code.

    Args:
        sender_filter: partial match on sender address (e.g. 'enbridge', 'okta')
        max_wait: max seconds to wait for the email
        poll_interval: seconds between polls
    """
    start = time.time()
    seen_keys = set()

    while time.time() - start < max_wait:
        code = _check_for_code(sender_filter, seen_keys)
        if code:
            return code
        time.sleep(poll_interval)

    return ""


def _check_for_code(sender_filter: str, seen_keys: set) -> str:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
    objects = resp.get("Contents", [])

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
    recent = [
        o for o in objects
        if o["LastModified"] > cutoff and o["Key"] not in seen_keys
    ]
    recent.sort(key=lambda o: o["LastModified"], reverse=True)

    for obj in recent:
        seen_keys.add(obj["Key"])
        try:
            raw = s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
            msg = email.message_from_bytes(raw)

            sender = msg.get("From", "").lower()
            if sender_filter and sender_filter.lower() not in sender:
                continue

            body = _get_body(msg)
            code = _extract_code(body)
            if code:
                return code
        except Exception:
            continue

    return ""


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
            elif ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="ignore")
    return ""


def _extract_code(body: str) -> str:
    for pattern in MFA_PATTERNS:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""
