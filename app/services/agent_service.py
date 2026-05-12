"""
Agent Service.

Single implementation — the LLM backend is injected via llm_client.
Model-specific behaviour (tag stripping, message format) lives in the
respective LLM client (BedrockClient, LLMClient), not here.

Backend selection via AGENT_BACKEND env var:
  "bedrock"  — Amazon Bedrock Nova Lite (default)
  "gpt"      — OpenAI GPT-4o mini

Usage:
    from app.services.agent_service import AgentService
    agent = AgentService()   # picks backend from settings automatically
"""

import json
import logging
from typing import Dict, List

from app.config import settings
from app.tools import TOOLS, SYSTEM_PROMPT  # noqa: F401 — re-exported for convenience

logger = logging.getLogger(__name__)


class AgentService:
    """
    LLM agent with tool calling.

    Runs the agent loop: send message → execute tool calls → repeat until
    the model returns a text response. The LLM client handles all
    model-specific formatting (Bedrock Converse API vs OpenAI API).
    """

    def __init__(self, llm_client=None):
        if llm_client is not None:
            self.llm_client = llm_client
        elif settings.AGENT_BACKEND == "gpt":
            from app.llm_client import LLMClient
            self.llm_client = LLMClient()
        else:
            from app.bedrock_client import BedrockClient
            self.llm_client = BedrockClient()

    async def run(
        self,
        user_message: str,
        history: List[Dict],
        tool_executor,
    ) -> str:
        """
        Run one turn of the agent loop.

        Args:
            user_message: The user's latest message
            history: Previous messages [{role, content}, ...]
            tool_executor: async callable(tool_name, args) -> str

        Returns:
            The assistant's final text response, or a special marker string:
            - "INVOICE_PDF:<filename>:<base64>" — PDF to send as file
            - "INSTAGRAM_CONNECT_URL:<url>"     — URL to present to user
        """
        from datetime import date
        today = date.today().isoformat()

        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=today)}]

        # Filter history: only user/assistant/tool roles,
        # conversation must start with a user message.
        filtered = []
        for msg in history:
            role = msg.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            if not filtered and role != "user":
                continue
            filtered.append(msg)

        messages.extend(filtered)
        messages.append({"role": "user", "content": user_message})

        for _ in range(10):  # max 10 tool-call rounds per turn
            response = await self.llm_client.call_llm(
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
                tools=TOOLS,
                tool_choice="auto",
            )

            choice = response.get("choices", [{}])[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "stop")
            messages.append(message)

            if finish_reason == "tool_calls" or message.get("tool_calls"):
                for tool_call in message.get("tool_calls", []):
                    tool_name = tool_call["function"]["name"]
                    try:
                        args = json.loads(tool_call["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}

                    logger.info(f"Tool call: {tool_name}({args})")
                    try:
                        result = await tool_executor(tool_name, args)
                    except Exception as e:
                        result = f"Error: {str(e)}"
                    logger.info(f"Tool result: {str(result)[:200]}")

                    # Special returns — pass through without going back to LLM
                    if isinstance(result, str) and result.startswith("INVOICE_PDF:"):
                        return result
                    if isinstance(result, str) and result.startswith("INSTAGRAM_CONNECT_URL:"):
                        return result

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": str(result),
                    })
            else:
                content = message.get("content", "")
                return content.strip() if content else ""

        return "I ran into an issue processing your request. Please try again."

    async def close(self):
        await self.llm_client.close()
