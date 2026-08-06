import json
import pytest
from unittest.mock import patch, MagicMock
from tools.billing import get_cost_split, split_bill, split_all_bills
from tools.base import ToolResult
from tools.storage import upload_bill


class TestGetCostSplit:
    def test_windmill(self, aws_env):
        split = get_cost_split("windmill")
        assert split == {"landlord_pct": 30, "tenant_pct": 70}

    def test_bellcrest(self, aws_env):
        split = get_cost_split("bellcrest")
        assert split == {"landlord_pct": 66, "tenant_pct": 34}

    def test_unknown_defaults_to_100(self, aws_env):
        split = get_cost_split("nonexistent")
        assert split == {"landlord_pct": 100, "tenant_pct": 0}


class TestSplitBill:
    def _mock_bedrock_amount(self, amount_str):
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"type": "text", "text": amount_str}]
        }).encode()
        return {"body": mock_body}

    def test_split_windmill(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "windmill", "alectra", "2026-08")

        mock_read = ToolResult(success=True, data={"content": "Total Amount Due: $329.71"})

        with patch("tools.billing.read_bill_pdf", return_value=mock_read), \
             patch("tools.billing.bedrock") as mock_bedrock:
            mock_bedrock.invoke_model.return_value = self._mock_bedrock_amount("329.71")
            result = split_bill("windmill", "alectra", "2026-08")

        assert result.success
        assert result.data["total"] == 329.71
        assert result.data["landlord_pct"] == 30
        assert result.data["tenant_pct"] == 70
        assert result.data["landlord_amount"] == 98.91
        assert result.data["tenant_amount"] == 230.80

    def test_split_bellcrest(self, aws_env, tmp_path):
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-fake")
        upload_bill(str(pdf), "bellcrest", "alectra", "2026-08")

        mock_read = ToolResult(success=True, data={"content": "Total: $337.14"})

        with patch("tools.billing.read_bill_pdf", return_value=mock_read), \
             patch("tools.billing.bedrock") as mock_bedrock:
            mock_bedrock.invoke_model.return_value = self._mock_bedrock_amount("337.14")
            result = split_bill("bellcrest", "alectra", "2026-08")

        assert result.success
        assert result.data["total"] == 337.14
        assert result.data["landlord_amount"] == 222.51
        assert result.data["tenant_amount"] == 114.63

    def test_bill_not_found(self, aws_env):
        mock_read = ToolResult(success=False, error="Bill not found. Download it first.")
        with patch("tools.billing.read_bill_pdf", return_value=mock_read):
            result = split_bill("windmill", "alectra", "2099-01")
        assert not result.success

    def test_rounding(self, aws_env):
        mock_read = ToolResult(success=True, data={"content": "Total: $100.00"})

        with patch("tools.billing.read_bill_pdf", return_value=mock_read), \
             patch("tools.billing.bedrock") as mock_bedrock:
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": "100.00"}]
            }).encode()
            mock_bedrock.invoke_model.return_value = {"body": mock_body}
            result = split_bill("windmill", "alectra", "2026-08")

        assert result.data["landlord_amount"] == 30.0
        assert result.data["tenant_amount"] == 70.0


class TestSplitAllBills:
    def _mock_bedrock_amount(self, amount_str):
        mock_body = MagicMock()
        mock_body.read.return_value = json.dumps({
            "content": [{"type": "text", "text": amount_str}]
        }).encode()
        return {"body": mock_body}

    def test_aggregates(self, aws_env):
        amounts = {"windmill": "200.00", "bellcrest": "300.00"}
        call_count = {"n": 0}
        props = ["windmill", "bellcrest"]

        def mock_read(prop, provider, month, question=""):
            return ToolResult(success=True, data={"content": f"Total: ${amounts[prop]}"})

        def mock_invoke(**kwargs):
            prop = props[call_count["n"] % 2]
            call_count["n"] += 1
            mock_body = MagicMock()
            mock_body.read.return_value = json.dumps({
                "content": [{"type": "text", "text": amounts[prop]}]
            }).encode()
            return {"body": mock_body}

        with patch("tools.billing.read_bill_pdf", side_effect=mock_read), \
             patch("tools.billing.bedrock") as mock_bedrock:
            mock_bedrock.invoke_model.side_effect = mock_invoke
            result = split_all_bills("2026-08")

        assert result.success
        assert len(result.data["bills"]) > 0
        assert result.data["landlord_total"] > 0
