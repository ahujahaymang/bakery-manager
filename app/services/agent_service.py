"""
Agent Service.

Architecture: Plan → Execute → Summarise

Every request goes through exactly 2 LLM calls (or 1 if no tools needed):

  1. Plan call   — LLM receives the message and returns ALL tool calls it
                   wants to make. No execution happens here.

  2. Execute     — All tool calls run in parallel (asyncio.gather).
                   No LLM involved. Pure service layer calls.

  3. Summarise   — LLM receives all results and returns a single
                   user-facing response. tool_choice="none" so it
                   cannot trigger more tool calls.

This replaces the old loop-based approach (1 LLM call per tool-call round)
with a fixed 2-call pattern regardless of how many tools are needed.

Backend selection via AGENT_BACKEND env var:
  "bedrock"  — Amazon Bedrock Nova Lite (default)
  "gpt"      — OpenAI GPT-4o mini (currently active)
"""

import asyncio
import json
import logging
from typing import Dict, List

from app.config import settings
from app.tools import TOOLS, SYSTEM_PROMPT  # noqa: F401 — re-exported for convenience

logger = logging.getLogger(__name__)


class AgentService:
    """
    LLM agent: Plan → Execute → Summarise.

    The LLM is called exactly twice per turn:
      - Once to decide what to do (planning)
      - Once to explain what happened (summarising)

    Tool execution is completely separate from LLM calls.
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
        Process a user message: Plan → Execute → Summarise.

        Args:
            user_message:  The user's latest message
            history:       Previous messages [{role, content}, ...]
            tool_executor: async callable(tool_name, args) -> str

        Returns:
            User-facing response string, or a special marker:
            - "INVOICE_PDF:<filename>:<base64>"
            - "INSTAGRAM_CONNECT_URL:<url>"
        """
        from datetime import date
        today = date.today().isoformat()

        # Build message list: system + filtered history + new user message
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(today=today)}]
        messages.extend(self._filter_history(history))
        messages.append({"role": "user", "content": user_message})

        # ── Step 1: Plan ───────────────────────────────────────────────────
        # Ask the LLM what tools to call (and with what args).
        # The LLM returns either:
        #   a) tool_calls — a list of {name, args} to execute
        #   b) text       — a direct answer (no tools needed)
        plan_response = await self.llm_client.call_llm(
            messages=messages,
            temperature=0.3,
            max_tokens=4000,
            tools=TOOLS,
            tool_choice="auto",
        )

        plan_message = plan_response.get("choices", [{}])[0].get("message", {})
        tool_calls = plan_message.get("tool_calls", [])

        # No tools needed — return the direct text response
        if not tool_calls:
            content = plan_message.get("content", "")
            return content.strip() if content else ""

        logger.info(f"Plan: {len(tool_calls)} tool call(s): "
                    f"{[tc['function']['name'] for tc in tool_calls]}")

        messages.append(plan_message)

        # ── Step 2: Execute ────────────────────────────────────────────────
        # Run all planned tool calls in parallel. No LLM involved.
        tool_results = await self._execute_parallel(tool_calls, tool_executor)

        # Check for special pass-through results (PDF, Instagram URL, CHOOSE: picker)
        for _, result in tool_results:
            if isinstance(result, str) and result.startswith("INVOICE_PDF:"):
                return result
            if isinstance(result, str) and result.startswith("INSTAGRAM_CONNECT_URL:"):
                return result
            if isinstance(result, str) and "CHOOSE:" in result:
                # Pass CHOOSE: markers directly — don't let summarise reformat them
                return result

        # Append all tool results to the message list
        for tool_call_id, result in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result,
            })

        # ── Step 3: Summarise ──────────────────────────────────────────────
        # Ask the LLM to turn the tool results into a user-facing response.
        # tool_choice="none" prevents it from triggering more tool calls.
        summary_response = await self.llm_client.call_llm(
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
            tools=TOOLS,
            tool_choice="none",
        )

        content = summary_response.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip() if content else "Done."

    # ── Internal helpers ───────────────────────────────────────────────────

    def _filter_history(self, history: List[Dict]) -> List[Dict]:
        """
        Filter conversation history for LLM compatibility:
        - Only user/assistant/tool roles
        - Must start with a user message
        """
        filtered = []
        for msg in history:
            role = msg.get("role")
            if role not in ("user", "assistant", "tool"):
                continue
            if not filtered and role != "user":
                continue
            filtered.append(msg)
        return filtered

    async def _execute_parallel(
        self,
        tool_calls: List[Dict],
        tool_executor,
    ) -> List[tuple]:
        """
        Execute all tool calls concurrently.
        Returns list of (tool_call_id, result_str) in the same order.
        """
        async def _run_one(tool_call):
            tool_name = tool_call["function"]["name"]
            try:
                args = json.loads(tool_call["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            logger.info(f"Executing: {tool_name}({args})")
            try:
                result = await tool_executor(tool_name, args)
            except Exception as e:
                result = f"Error: {str(e)}"
            logger.info(f"Result: {str(result)[:150]}")
            return tool_call["id"], str(result)

        return list(await asyncio.gather(*[_run_one(tc) for tc in tool_calls]))

    async def close(self):
        await self.llm_client.close()
