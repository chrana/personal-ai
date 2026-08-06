import os
import json
import pytest
import boto3
from moto import mock_aws

os.environ["TESTING"] = "1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"

TEST_CONFIG = {
    "properties": {
        "windmill": {
            "address": "42 Windmill Blvd, Lot 238, Brampton ON L6Y 3E4",
            "utilities": {
                "enbridge": {"account": "910059477804"},
                "peel-water": {},
                "alectra": {},
            },
            "cost_split": {"landlord_pct": 30, "tenant_pct": 70},
            "credentials": "personal-ai/creds/windmill",
        },
        "bellcrest": {
            "address": "49 Bellcrest Rd, Brampton",
            "utilities": {
                "enbridge": {"account": "PLACEHOLDER"},
                "peel-water": {},
                "alectra": {},
            },
            "cost_split": {"landlord_pct": 66, "tenant_pct": 34},
            "credentials": "personal-ai/creds/bellcrest",
        },
    }
}


@pytest.fixture
def aws_env(monkeypatch):
    with mock_aws():
        # Create S3 bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        from tools.storage import BUCKET
        s3.create_bucket(Bucket=BUCKET)

        # Create secrets
        sm = boto3.client("secretsmanager", region_name="us-east-1")
        sm.create_secret(Name="personal-ai/config", SecretString=json.dumps(TEST_CONFIG))
        sm.create_secret(
            Name="personal-ai/creds/windmill",
            SecretString=json.dumps({
                "enbridge": {"username": "test", "password": "test", "totp_secret": "AAAA", "mfa_method": "totp"},
                "peel-water": {"username": "test", "password": "test", "mfa_method": "email"},
                "alectra": {"username": "test", "password": "test", "mfa_method": "none"},
            }),
        )
        sm.create_secret(
            Name="personal-ai/creds/bellcrest",
            SecretString=json.dumps({
                "enbridge": {"username": "test", "password": "test", "totp_secret": "BBBB"},
                "peel-water": {"username": "test", "password": "test", "mfa_method": "email"},
                "alectra": {"username": "test", "password": "test", "mfa_method": "none"},
            }),
        )

        yield {"s3": s3, "sm": sm}
