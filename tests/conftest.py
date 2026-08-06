import os
import json
import pytest
import boto3
from moto import mock_aws

os.environ["TESTING"] = "1"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"



@pytest.fixture
def aws_env(monkeypatch):
    with mock_aws():
        # Create S3 bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        from tools.storage import BUCKET
        s3.create_bucket(Bucket=BUCKET)

        # Create credential secrets
        sm = boto3.client("secretsmanager", region_name="us-east-1")
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
