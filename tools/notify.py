import os
import boto3
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

ses = boto3.client("ses", region_name="us-east-1")

SENDER = "bills@mfa.ranachirag.com"
RECIPIENTS = ["chrana1413@gmail.com", "ritikakb1@gmail.com"]


def send_bill_summary(subject: str, body: str, attachments: list = None):
    """Send summary email with optional PDF attachments.

    attachments: list of {"filename": "name.pdf", "path": "/path/to/file.pdf"}
    """
    if not attachments:
        for recipient in RECIPIENTS:
            try:
                ses.send_email(
                    Source=SENDER,
                    Destination={"ToAddresses": [recipient]},
                    Message={
                        "Subject": {"Data": subject, "Charset": "UTF-8"},
                        "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
                    },
                )
            except Exception:
                pass
        return

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = SENDER

    msg.attach(MIMEText(body, "plain", "utf-8"))

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
