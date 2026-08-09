import os
import time
import json
import logging
import boto3

LOG_GROUP = "personal-ai"
NAMESPACE = "PersonalAI"

logger = logging.getLogger("personal-ai")
logger.setLevel(logging.INFO)

if os.environ.get("TESTING") != "1":
    import watchtower
    cloudwatch = boto3.client("cloudwatch", region_name="us-east-1")
    cw_handler = watchtower.CloudWatchLogHandler(
        log_group_name=LOG_GROUP,
        stream_name="app",
        boto3_client=boto3.client("logs", region_name="us-east-1"),
    )
    cw_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(cw_handler)
else:
    cloudwatch = None
    logger.addHandler(logging.StreamHandler())


def log_request(method: str, path: str, status: int, duration_ms: float, **extra):
    entry = {
        "type": "request",
        "method": method,
        "path": path,
        "status": status,
        "duration_ms": round(duration_ms, 1),
        **extra,
    }
    logger.info(json.dumps(entry))
    _put_metric("RequestLatency", duration_ms, "Milliseconds", {"Endpoint": path})
    _put_metric("RequestCount", 1, "Count", {"Endpoint": path})
    if status >= 400:
        _put_metric("ErrorCount", 1, "Count", {"Endpoint": path})


def log_tool_call(tool_name: str, success: bool, duration_ms: float, **extra):
    entry = {
        "type": "tool_call",
        "tool": tool_name,
        "success": success,
        "duration_ms": round(duration_ms, 1),
        **extra,
    }
    logger.info(json.dumps(entry))
    _put_metric("ToolCallDuration", duration_ms, "Milliseconds", {"Tool": tool_name})
    _put_metric("ToolCallCount", 1, "Count", {"Tool": tool_name})
    if not success:
        _put_metric("ToolCallErrors", 1, "Count", {"Tool": tool_name})


def log_bedrock_call(model: str, input_tokens: int, output_tokens: int, duration_ms: float):
    entry = {
        "type": "bedrock_call",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "duration_ms": round(duration_ms, 1),
    }
    logger.info(json.dumps(entry))
    _put_metric("BedrockLatency", duration_ms, "Milliseconds", {"Model": model})
    _put_metric("InputTokens", input_tokens, "Count", {"Model": model})
    _put_metric("OutputTokens", output_tokens, "Count", {"Model": model})


def log_browser(provider: str, property_slug: str, success: bool, duration_ms: float, **extra):
    entry = {
        "type": "browser",
        "provider": provider,
        "property": property_slug,
        "success": success,
        "duration_ms": round(duration_ms, 1),
        **extra,
    }
    logger.info(json.dumps(entry))
    _put_metric("BrowserDuration", duration_ms, "Milliseconds", {"Provider": provider})
    if not success:
        _put_metric("BrowserErrors", 1, "Count", {"Provider": provider})


def log_orchestrator_error(error_type: str, detail: any, **extra):
    entry = {
        "type": "orchestrator_error",
        "error_type": error_type,
        "detail": detail,
        **extra,
    }
    logger.warning(json.dumps(entry))
    _put_metric("OrchestratorErrors", 1, "Count", {"ErrorType": error_type})


def _put_metric(name: str, value: float, unit: str, dimensions: dict):
    if not cloudwatch:
        return
    try:
        cloudwatch.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[{
                "MetricName": name,
                "Value": value,
                "Unit": unit,
                "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
            }],
        )
    except Exception:
        pass
