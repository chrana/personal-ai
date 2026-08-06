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
                "enbridge": {"account": "910059477804", "credentials": "personal-ai/creds/windmill/enbridge"},
                "peel-water": {"credentials": "personal-ai/creds/windmill/peel-water"},
                "alectra": {"credentials": "personal-ai/creds/windmill/alectra"},
            },
            "cost_split": {"landlord_pct": 30, "tenant_pct": 70},
        },
        "bellcrest": {
            "address": "49 Bellcrest Rd, Brampton",
            "utilities": {
                "enbridge": {"account": "PLACEHOLDER", "credentials": "personal-ai/creds/bellcrest/enbridge"},
                "peel-water": {"credentials": "personal-ai/creds/bellcrest/peel-water"},
                "alectra": {"credentials": "personal-ai/creds/bellcrest/alectra"},
            },
            "cost_split": {"landlord_pct": 66, "tenant_pct": 34},
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
            Name="personal-ai/creds/windmill/enbridge",
            SecretString=json.dumps({"username": "test", "password": "test", "totp_secret": "AAAA", "mfa_method": "totp"}),
        )
        sm.create_secret(
            Name="personal-ai/creds/windmill/alectra",
            SecretString=json.dumps({"username": "test", "password": "test", "mfa_method": "none"}),
        )
        sm.create_secret(
            Name="personal-ai/creds/bellcrest/alectra",
            SecretString=json.dumps({"username": "test", "password": "test", "mfa_method": "none"}),
        )

        yield {"s3": s3, "sm": sm}
