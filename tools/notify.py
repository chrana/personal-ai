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
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 0; padding: 16px; background: #f5f5f5; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
h1 {{ font-size: 20px; color: #1a1a1a; margin: 0 0 4px; }}
h2 {{ font-size: 16px; color: #333; margin: 16px 0 8px; }}
.subtitle {{ color: #666; font-size: 14px; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th {{ text-align: left; padding: 8px 4px; color: #666; font-weight: 500; border-bottom: 1px solid #eee; }}
td {{ padding: 8px 4px; border-bottom: 1px solid #f0f0f0; }}
.amount {{ text-align: right; font-weight: 600; }}
.tenant {{ text-align: right; color: #e65100; }}
.period {{ font-size: 12px; color: #999; }}
.total-row td {{ border-top: 2px solid #333; font-weight: 700; border-bottom: none; }}
.summary {{ background: #1a237e; color: #fff; border-radius: 12px; padding: 20px; }}
.summary h2 {{ color: #fff; margin: 0 0 12px; }}
.summary-item {{ display: flex; justify-content: space-between; padding: 6px 0; font-size: 15px; }}
.summary-amount {{ font-weight: 700; font-size: 18px; }}
</style></head><body>
<div class="card">
<h1>Bills Summary</h1>
<div class="subtitle">{usage_month} usage period</div>
"""

    for prop in ["windmill", "bellcrest"]:
        prop_bills = [b for b in data["bills"] if b["property"] == prop]
        if not prop_bills:
            continue
        split_pct = prop_bills[0]["tenant_pct"]
        prop_total = sum(b["total"] for b in prop_bills)
        tenant_total = sum(b["tenant_amount"] for b in prop_bills)

        html += f"""<h2>{prop.title()} <span style="color:#999;font-weight:400;font-size:13px;">tenant pays {split_pct}%</span></h2>
<table>
<tr><th>Provider</th><th class="amount">Total</th><th class="tenant">Tenant</th></tr>
"""
        for b in prop_bills:
            period = ""
            if b.get("billing_period_start") and b.get("billing_period_end"):
                period = f'<br><span class="period">{b["billing_period_start"]} to {b["billing_period_end"]}</span>'
            due = f'<br><span class="period">due {b["due_date"]}</span>' if b.get("due_date") else ""
            html += f'<tr><td>{b["provider"].title()}{period}</td><td class="amount">${b["total"]:.2f}{due}</td><td class="tenant">${b["tenant_amount"]:.2f}</td></tr>\n'

        html += f'<tr class="total-row"><td>Total</td><td class="amount">${prop_total:.2f}</td><td class="tenant">${tenant_total:.2f}</td></tr>\n</table>\n'

    html += "</div>\n"

    html += f"""<div class="summary">
<h2>Your Share (Landlord)</h2>
<div class="summary-item"><span>Total</span><span class="summary-amount">${data['landlord_total']:.2f}</span></div>
"""
    for prop, amount in data["tenant_totals"].items():
        html += f'<div class="summary-item"><span>{prop.title()} tenant owes</span><span class="summary-amount">${amount:.2f}</span></div>\n'

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
