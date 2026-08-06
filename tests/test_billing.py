import json
import pytest
from unittest.mock import patch, MagicMock
from tools.billing import get_cost_split, split_bill, split_by_usage_month
from tools.base import ToolResult
from tools.storage import upload_bill, save_bill_metadata


class TestGetCostSplit:
    def test_windmill(self, aws_env):
        split = get_cost_split("windmill")
        assert split == {"landlord_pct": 30, "tenant_pct": 70}

    def test_bellcrest(self, aws_env):
        split = get_cost_split("bellcrest")
        assert split == {"landlord_pct": 67, "tenant_pct": 33}

    def test_unknown_defaults_to_100(self, aws_env):
        split = get_cost_split("nonexistent")
        assert split == {"landlord_pct": 100, "tenant_pct": 0}


class TestSplitBill:
    def test_split_windmill(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "windmill", "alectra", "2026-08")
        save_bill_metadata("windmill", "alectra", "2026-08", {
            "property": "windmill",
            "provider": "alectra",
            "bill_month": "2026-08",
            "usage_month": "2026-07",
            "billing_period_start": "2026-06-23",
            "billing_period_end": "2026-07-24",
            "due_date": "2026-08-24",
            "total_amount": 329.71,
            "s3_key": "bills/windmill/alectra/2026-08.pdf",
        })

        result = split_bill("windmill", "alectra", "2026-08")

        assert result.success
        assert result.data["total"] == 329.71
        assert result.data["landlord_pct"] == 30
        assert result.data["tenant_pct"] == 70
        assert result.data["landlord_amount"] == 98.91
        assert result.data["tenant_amount"] == 230.80
        assert result.data["usage_month"] == "2026-07"

    def test_split_bellcrest(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "bellcrest", "alectra", "2026-08")
        save_bill_metadata("bellcrest", "alectra", "2026-08", {
            "property": "bellcrest",
            "provider": "alectra",
            "bill_month": "2026-08",
            "usage_month": "2026-07",
            "billing_period_start": "2026-06-18",
            "billing_period_end": "2026-07-21",
            "due_date": "2026-08-20",
            "total_amount": 337.14,
            "s3_key": "bills/bellcrest/alectra/2026-08.pdf",
        })

        result = split_bill("bellcrest", "alectra", "2026-08")

        assert result.success
        assert result.data["total"] == 337.14
        assert result.data["landlord_amount"] == 225.88
        assert result.data["tenant_amount"] == 111.26

    def test_no_metadata(self, aws_env):
        result = split_bill("windmill", "alectra", "2099-01")
        assert not result.success
        assert "metadata" in result.error.lower() or "No metadata" in result.error

    def test_rounding(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "windmill", "alectra", "2026-09")
        save_bill_metadata("windmill", "alectra", "2026-09", {
            "property": "windmill",
            "provider": "alectra",
            "bill_month": "2026-09",
            "usage_month": "2026-08",
            "total_amount": 100.00,
            "s3_key": "bills/windmill/alectra/2026-09.pdf",
        })

        result = split_bill("windmill", "alectra", "2026-09")
        assert result.data["landlord_amount"] == 30.0
        assert result.data["tenant_amount"] == 70.0


class TestSplitByUsageMonth:
    def test_aggregates(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")

        upload_bill(str(pdf), "windmill", "alectra", "2026-08")
        save_bill_metadata("windmill", "alectra", "2026-08", {
            "property": "windmill",
            "provider": "alectra",
            "bill_month": "2026-08",
            "usage_month": "2026-07",
            "total_amount": 200.00,
            "s3_key": "bills/windmill/alectra/2026-08.pdf",
        })

        upload_bill(str(pdf), "bellcrest", "alectra", "2026-08")
        save_bill_metadata("bellcrest", "alectra", "2026-08", {
            "property": "bellcrest",
            "provider": "alectra",
            "bill_month": "2026-08",
            "usage_month": "2026-07",
            "total_amount": 300.00,
            "s3_key": "bills/bellcrest/alectra/2026-08.pdf",
        })

        result = split_by_usage_month("2026-07")

        assert result.success
        assert len(result.data["bills"]) == 2
        assert result.data["landlord_total"] == 261.0  # 200*0.30 + 300*0.67
        assert result.data["tenant_totals"]["windmill"] == 140.0
        assert result.data["tenant_totals"]["bellcrest"] == 99.0

    def test_deduplicates(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")

        # Same bill saved under two different months
        for month in ["2026-07", "2026-08"]:
            upload_bill(str(pdf), "windmill", "enbridge", month)
            save_bill_metadata("windmill", "enbridge", month, {
                "property": "windmill",
                "provider": "enbridge",
                "bill_month": month,
                "usage_month": "2026-07",
                "total_amount": 100.00,
                "s3_key": f"bills/windmill/enbridge/{month}.pdf",
            })

        result = split_by_usage_month("2026-07")
        assert len(result.data["bills"]) == 1

    def test_empty_month(self, aws_env):
        result = split_by_usage_month("2099-01")
        assert result.success
        assert result.data["bills"] == []
