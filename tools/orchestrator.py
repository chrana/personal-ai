import json
import boto3
from tools.base import Tool, ToolResult
from tools.browser import BrowserTool

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
MODEL = "us.anthropic.claude-sonnet-4-6"

TOOLS: dict[str, Tool] = {
    "browser": BrowserTool(),
}

TOOL_DEFINITIONS = [
    {
        "name": "download_utility_bill",
        "description": "Download a utility bill PDF from a provider website. Checks S3 cache first, only uses browser if not cached. Properties: 42-windmill-brampton, 49-bellcrest-brampton, 43-blair-athol-toronto, 673-rosselini-mississauga. Providers: enbridge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Utility provider key (e.g. 'enbridge')",
                },
                "property": {
                    "type": "string",
                    "description": "Property slug or address keyword (e.g. '42-windmill-brampton', 'windmill', 'bellcrest', 'blair', 'rosselini')",
                },
                "bill_month": {
                    "type": "string",
                    "description": "Bill month in YYYY-MM format",
                },
            },
            "required": ["provider", "property", "bill_month"],
        },
    },
]


async def run_tool_call(name: str, input_data: dict) -> ToolResult:
    if name == "download_utility_bill":
        return await TOOLS["browser"].run(action="download_utility_bill", **input_data)
    return ToolResult(success=False, error=f"Unknown tool: {name}")


async def orchestrate(user_message: str, system_context: str = "") -> dict:
    messages = [{"role": "user", "content": user_message}]

    system = "You are a personal AI assistant with tool access. Use tools when the user asks you to perform actions. Be concise."
    if system_context:
        system += f"\n\n{system_context}"

    response = bedrock.invoke_model(
        modelId=MODEL,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": system,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
        }),
    )

    result = json.loads(response["body"].read())
    stop_reason = result.get("stop_reason")

    if stop_reason == "tool_use":
        tool_results = []
        assistant_content = result["content"]

        for block in assistant_content:
            if block["type"] == "tool_use":
                tool_result = await run_tool_call(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": json.dumps(tool_result.data if tool_result.success else {"error": tool_result.error}),
                })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

        final_response = bedrock.invoke_model(
            modelId=MODEL,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1024,
                "system": system,
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
            }),
        )
        final_result = json.loads(final_response["body"].read())
        text = next((b["text"] for b in final_result["content"] if b["type"] == "text"), "")
        return {"response": text, "tools_used": [b["name"] for b in assistant_content if b["type"] == "tool_use"]}

    text = next((b["text"] for b in result["content"] if b["type"] == "text"), "")
    return {"response": text, "tools_used": []}
