import os
import pytest
from moto import mock_aws
from tools.storage import s3_key, bill_exists, upload_bill, download_bill, list_bills, BUCKET


class TestS3Key:
    def test_basic(self):
        assert s3_key("windmill", "enbridge", "2026-08") == "bills/windmill/enbridge/2026-08.pdf"

    def test_different_provider(self):
        assert s3_key("bellcrest", "alectra", "2026-07") == "bills/bellcrest/alectra/2026-07.pdf"


class TestBillExists:
    def test_not_found(self, aws_env):
        assert bill_exists("windmill", "enbridge", "2026-01") is False

    def test_found(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "windmill", "enbridge", "2026-08")
        assert bill_exists("windmill", "enbridge", "2026-08") is True


class TestUploadDownload:
    def test_round_trip(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content here")
        upload_bill(str(pdf), "windmill", "alectra", "2026-08")

        local = download_bill("windmill", "alectra", "2026-08")
        assert os.path.exists(local)
        with open(local, "rb") as f:
            assert f.read() == b"%PDF-1.4 fake content here"


class TestListBills:
    def test_empty(self, aws_env):
        assert list_bills() == []

    def test_lists_uploaded(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")
        upload_bill(str(pdf), "windmill", "enbridge", "2026-07")
        upload_bill(str(pdf), "windmill", "alectra", "2026-08")
        upload_bill(str(pdf), "bellcrest", "enbridge", "2026-08")

        all_bills = list_bills()
        assert len(all_bills) == 3

    def test_filter_by_property(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")
        upload_bill(str(pdf), "windmill", "enbridge", "2026-07")
        upload_bill(str(pdf), "bellcrest", "enbridge", "2026-08")

        bills = list_bills(property_slug="windmill")
        assert len(bills) == 1
        assert bills[0]["property"] == "windmill"

    def test_filter_by_property_and_provider(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF")
        upload_bill(str(pdf), "windmill", "enbridge", "2026-07")
        upload_bill(str(pdf), "windmill", "alectra", "2026-08")

        bills = list_bills(property_slug="windmill", provider="alectra")
        assert len(bills) == 1
        assert bills[0]["provider"] == "alectra"
