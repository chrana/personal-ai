import json
import time
import base64
import io
import boto3
from pdf2image import convert_from_path
from tools.base import Tool, ToolResult
from tools.browser import BrowserTool
from tools.secrets import get_secret
from tools.storage import bill_exists, download_bill
from tools.monitoring import log_tool_call, log_bedrock_call

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"

TOOLS: dict[str, Tool] = {
    "browser": BrowserTool(),
}

TOOL_DEFINITIONS = [
    {
        "name": "download_utility_bill",
        "description": "Download a utility bill PDF from a provider website. Checks S3 cache first, only uses browser if not cached.",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Utility provider key (e.g. 'enbridge', 'peel-water', 'alectra')",
                },
                "property": {
                    "type": "string",
                    "description": "Property name (e.g. 'windmill', 'bellcrest', 'blair-athol', 'rosselini')",
                },
                "bill_month": {
                    "type": "string",
                    "description": "Bill month in YYYY-MM format",
                },
            },
            "required": ["provider", "property", "bill_month"],
        },
    },
    {
        "name": "read_bill",
        "description": "Read and extract text/data from a previously downloaded utility bill PDF. Use this to answer questions about bill amounts, usage, charges, due dates, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Utility provider key (e.g. 'enbridge', 'peel-water', 'alectra')",
                },
                "property": {
                    "type": "string",
                    "description": "Property name (e.g. 'windmill', 'bellcrest', 'blair-athol', 'rosselini')",
                },
                "bill_month": {
                    "type": "string",
                    "description": "Bill month in YYYY-MM format",
                },
                "question": {
                    "type": "string",
                    "description": "What to extract or analyze from the bill",
                },
            },
            "required": ["provider", "property", "bill_month"],
        },
    },
]


def read_bill_pdf(property_slug: str, provider: str, bill_month: str, question: str = "") -> ToolResult:
    if not bill_exists(property_slug, provider, bill_month):
        return ToolResult(success=False, error=f"Bill not found. Download it first.")

    local_path = download_bill(property_slug, provider, bill_month)

    images = convert_from_path(local_path, dpi=150)
    image_contents = []
    for img in images[:2]:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        image_contents.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })

    prompt = question or "Extract all key information from this utility bill: account number, billing period, total amount, usage, charges breakdown, due date, and any other relevant details. Return as structured text."

    response = bedrock.invoke_model(
        modelId=MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": image_contents + [{"type": "text", "text": prompt}]}],
        }),
    )

    result = json.loads(response["body"].read())
    extracted = result["content"][0]["text"]
    return ToolResult(success=True, data={"content": extracted, "property": property_slug, "provider": provider, "bill_month": bill_month})


def get_property_context() -> str:
    try:
        config = get_secret("personal-ai/config")
        properties = config["properties"]
        lines = ["Your properties:"]
        for slug, info in properties.items():
            providers = ", ".join(info["utilities"].keys())
            lines.append(f"  - {slug}: {info['address']} (utilities: {providers})")
        return "\n".join(lines)
    except:
        return ""


SYSTEM_PROMPT = """You are a personal AI assistant for a property owner who manages multiple rental properties. You have tool access for downloading utility bills and other tasks.

{property_context}

When the user asks about "my bill" or a utility without specifying a property, ask which property they mean OR use context from the conversation to infer it. If they've been talking about a specific property, use that one.

When the user asks about bill contents, amounts, usage, or wants a summary — use the read_bill tool to extract the data from the PDF. You don't need to download first if the bill is already cached; read_bill checks S3 directly.

Be concise and helpful. Don't ask for information you already know from context."""


async def run_tool_call(name: str, input_data: dict) -> ToolResult:
    start = time.time()
    if name == "download_utility_bill":
        result = await TOOLS["browser"].run(action="download_utility_bill", **input_data)
    elif name == "read_bill":
        question = input_data.pop("question", "")
        from tools.browser import resolve_property
        property_slug = resolve_property(input_data["property"])
        if not property_slug:
            result = ToolResult(success=False, error=f"Unknown property: {input_data['property']}")
        else:
            result = read_bill_pdf(property_slug, input_data["provider"], input_data["bill_month"], question)
    else:
        result = ToolResult(success=False, error=f"Unknown tool: {name}")
    duration_ms = (time.time() - start) * 1000
    log_tool_call(name, result.success, duration_ms, **input_data)
    return result


async def orchestrate(messages: list, memory_context: str = "") -> dict:
    property_context = get_property_context()
    system = SYSTEM_PROMPT.format(property_context=property_context)

    if memory_context:
        system += f"\n\nRelevant memories from past conversations:\n{memory_context}"

    all_tools_used = []
    max_rounds = 5

    for _ in range(max_rounds):
        bedrock_start = time.time()
        response = bedrock.invoke_model(
            modelId=MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 2048,
                "system": system,
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
            }),
        )

        result = json.loads(response["body"].read())
        bedrock_ms = (time.time() - bedrock_start) * 1000
        usage = result.get("usage", {})
        log_bedrock_call(MODEL, usage.get("input_tokens", 0), usage.get("output_tokens", 0), bedrock_ms)
        stop_reason = result.get("stop_reason")

        if stop_reason != "tool_use":
            text = next((b["text"] for b in result["content"] if b["type"] == "text"), "")
            return {"response": text, "tools_used": all_tools_used}

        assistant_content = result["content"]
        tool_results = []

        for block in assistant_content:
            if block["type"] == "tool_use":
                all_tools_used.append(block["name"])
                tool_result = await run_tool_call(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error}),
                })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    text = next((b["text"] for b in result["content"] if b["type"] == "text"), "")
    return {"response": text, "tools_used": all_tools_used}
