import boto3

ses = boto3.client("ses", region_name="us-east-1")

SENDER = "bills@mfa.ranachirag.com"
RECIPIENTS = ["chrana1413@gmail.com", "ritikakb1@gmail.com"]


def send_bill_summary(subject: str, body: str):
    ses.send_email(
        Source=SENDER,
        Destination={"ToAddresses": RECIPIENTS},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
        },
    )
