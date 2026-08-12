import json
import time
import base64
import io
import boto3
from pdf2image import convert_from_path
from tools.base import Tool, ToolResult
from tools.browser import BrowserTool
from tools.storage import bill_exists, download_bill, list_bills
from config import PROPERTIES
from tools.monitoring import log_tool_call, log_bedrock_call, log_orchestrator_error

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"

TOOLS: dict[str, Tool] = {
    "browser": BrowserTool(),
}

TOOL_DEFINITIONS = [
    {
        "name": "split_bill",
        "description": "Calculate landlord/tenant cost split for a utility bill. Returns the total amount, each party's percentage, and their dollar amounts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Utility provider key (e.g. 'enbridge', 'peel-water', 'alectra', 'enercare')",
                },
                "property": {
                    "type": "string",
                    "description": "Property name (e.g. 'windmill', 'bellcrest')",
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
        "name": "split_all_bills",
        "description": "Calculate landlord/tenant cost split for ALL utility bills for a given usage month across all properties. Attribution is by usage period (when the utility was consumed), not when the bill was issued or due. Returns a full breakdown with totals.",
        "input_schema": {
            "type": "object",
            "properties": {
                "usage_month": {
                    "type": "string",
                    "description": "Usage month in YYYY-MM format (the month utilities were consumed, not billed)",
                },
            },
            "required": ["usage_month"],
        },
    },
    {
        "name": "download_utility_bill",
        "description": "Download a utility bill PDF from a provider website. Checks S3 cache first, only uses browser if not cached.",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Utility provider key (e.g. 'enbridge', 'peel-water', 'alectra', 'enercare')",
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
                    "description": "Utility provider key (e.g. 'enbridge', 'peel-water', 'alectra', 'enercare')",
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
    {
        "name": "list_bills",
        "description": "List all available bills stored in the system. Can filter by property and/or provider. Use this to find what bills are available before answering questions about totals, trends, or comparisons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "property": {
                    "type": "string",
                    "description": "Optional: filter by property (e.g. 'windmill', 'bellcrest')",
                },
                "provider": {
                    "type": "string",
                    "description": "Optional: filter by provider (e.g. 'enbridge', 'alectra', 'peel-water')",
                },
            },
            "required": [],
        },
    },
    {
        "name": "rent_balance",
        "description": "Check rent payment status: how much was expected, received, and outstanding for a given month. Also ingests any new e-transfer notifications before reporting.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Month in YYYY-MM format. If omitted, returns all-time totals.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "monthly_pnl",
        "description": "Get full profit & loss for a month: rent income received, utility expenses (landlord portion), and net income per property.",
        "input_schema": {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "description": "Month in YYYY-MM format",
                },
            },
            "required": ["month"],
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
    lines = ["Your properties:"]
    for slug, info in PROPERTIES.items():
        providers = ", ".join(info["utilities"])
        lines.append(f"  - {slug} (utilities: {providers})")
    return "\n".join(lines)


SYSTEM_PROMPT = """You are a personal AI assistant for a property owner who manages multiple rental properties. You have tool access for downloading utility bills, reading them, calculating cost splits, and answering questions.

{property_context}

Cost split: Windmill 30% landlord / 70% tenant. Bellcrest 67% landlord / 33% tenant.

Today's date: {today}

Guidelines:
- When the user asks about "my bill" or a utility without specifying a property, ask which property they mean OR use context from the conversation to infer it.
- For bill contents, amounts, usage, or summaries — use read_bill. You don't need to download first if it's cached.
- For aggregate questions ("total spend this quarter", "which property costs more", "compare months") — use list_bills to find available bills, then read_bill on each relevant one.
- For split/tenant questions — use split_bill or split_all_bills.
- When the user says "this month" or "last month", calculate from today's date.
- Be concise. Present numbers in a clear format. Don't ask for info you already know."""


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
    elif name == "split_bill":
        from tools.billing import split_bill
        from tools.browser import resolve_property
        property_slug = resolve_property(input_data["property"])
        if not property_slug:
            result = ToolResult(success=False, error=f"Unknown property: {input_data['property']}")
        else:
            result = split_bill(property_slug, input_data["provider"], input_data["bill_month"])
    elif name == "split_all_bills":
        from tools.billing import split_by_usage_month
        result = split_by_usage_month(input_data.get("usage_month", input_data.get("bill_month", "")))
    elif name == "list_bills":
        from tools.browser import resolve_property
        prop = input_data.get("property", "")
        property_slug = resolve_property(prop) if prop else ""
        bills = list_bills(property_slug, input_data.get("provider", ""))
        result = ToolResult(success=True, data={"bills": bills})
    elif name == "rent_balance":
        from tools.ledger import ingest_etransfers, get_balance
        ingest_etransfers()
        month = input_data.get("month", "")
        balance = get_balance(month if month else None)
        result = ToolResult(success=True, data=balance)
    elif name == "monthly_pnl":
        from tools.ledger import ingest_etransfers, get_monthly_summary
        ingest_etransfers()
        summary = get_monthly_summary(input_data["month"])
        result = ToolResult(success=True, data=summary)
    else:
        result = ToolResult(success=False, error=f"Unknown tool: {name}")
    duration_ms = (time.time() - start) * 1000
    log_tool_call(name, result.success, duration_ms, **input_data)
    return result


def _describe_tool_call(name: str, input_data: dict) -> str:
    prop = input_data.get("property", "")
    provider = input_data.get("provider", "")
    month = input_data.get("bill_month", input_data.get("usage_month", ""))
    if name == "download_utility_bill":
        return f"Downloading {prop}/{provider} {month}..."
    elif name == "read_bill":
        return f"Reading {prop}/{provider} {month}..."
    elif name == "split_bill":
        return f"Splitting {prop}/{provider} {month}..."
    elif name == "split_all_bills":
        return f"Calculating splits for {month}..."
    elif name == "list_bills":
        return f"Listing bills{' for ' + prop if prop else ''}..."
    elif name == "rent_balance":
        return f"Checking rent payments{' for ' + month if month else ''}..."
    elif name == "monthly_pnl":
        return f"Calculating P&L for {month}..."
    return f"Running {name}..."


async def orchestrate(messages: list, memory_context: str = "", on_progress=None) -> dict:
    from datetime import date
    property_context = get_property_context()
    system = SYSTEM_PROMPT.format(property_context=property_context, today=date.today().isoformat())

    if memory_context:
        system += f"\n\nRelevant memories from past conversations:\n{memory_context}"

    async def emit(msg):
        if on_progress:
            await on_progress(msg)

    all_tools_used = []
    max_rounds = 5

    await emit("Thinking...")

    for round_num in range(max_rounds):
        bedrock_start = time.time()
        response = bedrock.invoke_model(
            modelId=MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4096,
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

        if stop_reason == "max_tokens":
            log_orchestrator_error("truncated_response", usage.get("output_tokens", 0),
                                   user_message=messages[0]["content"] if messages else "")
            await emit("Response was too long, continuing...")
            messages.append({"role": "assistant", "content": result["content"]})
            messages.append({"role": "user", "content": [{"type": "text", "text": "Your response was cut off. Please continue, using tools as needed."}]})
            continue

        if stop_reason != "tool_use":
            text = next((b["text"] for b in result["content"] if b["type"] == "text"), "")
            return {"response": text, "tools_used": all_tools_used}

        assistant_content = result["content"]
        tool_results = []

        for block in assistant_content:
            if block["type"] == "tool_use":
                all_tools_used.append(block["name"])
                await emit(_describe_tool_call(block["name"], block["input"]))
                tool_result = await run_tool_call(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error}),
                })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    log_orchestrator_error("max_rounds_exhausted", max_rounds,
                           user_message=messages[0]["content"] if messages else "")
    text = next((b["text"] for b in result["content"] if b["type"] == "text"), "")
    if not text or stop_reason == "max_tokens":
        return {"response": "Sorry, I wasn't able to complete that request — it was too complex for a single turn. Try breaking it into smaller asks (e.g. one property or provider at a time).", "tools_used": all_tools_used, "error": True}
    return {"response": text, "tools_used": all_tools_used}
