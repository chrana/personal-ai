import os
import boto3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

ses = boto3.client("ses", region_name="us-east-1")

SENDER = "bills@mfa.ranachirag.com"
RECIPIENTS = ["chrana1413@gmail.com", "ritikakb1@gmail.com"]


def build_html_summary(data: dict, usage_month: str) -> str:
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; color: #1a1a1a; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
.subtitle {{ color: #666; font-size: 14px; margin-bottom: 20px; }}
.prop-header {{ font-size: 17px; font-weight: 700; margin: 0 0 4px; }}
.prop-sub {{ font-size: 13px; color: #666; margin-bottom: 12px; }}
.bill-line {{ font-size: 15px; padding: 4px 0; }}
.total-line {{ font-size: 16px; font-weight: 700; padding: 8px 0 0; border-top: 1px solid #eee; margin-top: 8px; }}
.copy-block {{ background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 8px; padding: 14px; margin: 12px 0; font-size: 15px; line-height: 1.8; }}
.period-note {{ font-size: 12px; color: #999; margin-top: 8px; }}
</style></head><body>
<div class="card">
<h1>Monthly Bills — {usage_month}</h1>
<div class="subtitle">Copy-paste tenant amounts below</div>
"""

    for prop in ["windmill", "bellcrest"]:
        prop_bills = [b for b in data["bills"] if b["property"] == prop]
        if not prop_bills:
            continue
        split_pct = prop_bills[0]["tenant_pct"]
        tenant_total = sum(b["tenant_amount"] for b in prop_bills)

        html += f'<div class="prop-header">{prop.title()}</div>\n'
        html += f'<div class="prop-sub">Tenant share: {split_pct}% of each bill</div>\n'

        # Copy-paste block
        html += '<div class="copy-block">\n'
        amounts = []
        for b in prop_bills:
            provider_name = b["provider"].replace("-", " ").title()
            html += f'{provider_name} - {b["tenant_amount"]:.2f}<br>\n'
            amounts.append(f'{b["tenant_amount"]:.2f}')

        formula = "+".join(amounts)
        html += f'<strong>Total = {formula} = {tenant_total:.2f}</strong>\n'
        html += '</div>\n'

        # Period details (smaller, FYI)
        html += '<div class="period-note">\n'
        for b in prop_bills:
            if b.get("billing_period_start") and b.get("billing_period_end"):
                html += f'{b["provider"].title()}: {b["billing_period_start"]} to {b["billing_period_end"]}'
                if b.get("due_date"):
                    html += f' (due {b["due_date"]})'
                html += '<br>\n'
        html += '</div>\n<br>\n'

    html += "</div>\n</body></html>"
    return html


def send_bill_summary(subject: str, body: str, attachments: list = None, html: str = None):
    """Send summary email with optional PDF attachments and HTML body."""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = SENDER

    # Body (HTML preferred, plain text fallback)
    body_part = MIMEMultipart("alternative")
    body_part.attach(MIMEText(body, "plain", "utf-8"))
    if html:
        body_part.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(body_part)

    if attachments:
        for att in attachments:
            with open(att["path"], "rb") as f:
                part = MIMEApplication(f.read(), Name=att["filename"])
            part["Content-Disposition"] = f'attachment; filename="{att["filename"]}"'
            msg.attach(part)

    for recipient in RECIPIENTS:
        try:
            msg["To"] = recipient
            ses.send_raw_email(
                Source=SENDER,
                Destinations=[recipient],
                RawMessage={"Data": msg.as_string()},
            )
        except Exception:
            pass
        finally:
            del msg["To"]
