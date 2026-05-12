"""
AWS Bedrock Client for Amazon Nova models.

Drop-in replacement for LLMClient — same call_llm() interface,
uses boto3 to call Amazon Bedrock instead of OpenAI HTTP API.

Supports:
- amazon.nova-lite-v1:0   (recommended — tool calling, vision, cheap)
- amazon.nova-micro-v1:0  (cheapest, text only, no vision)
- amazon.nova-pro-v1:0    (most capable)

Tool calling uses the same format as OpenAI — the agent_service.py
TOOLS list works without modification.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class BedrockClient:
    """
    AWS Bedrock client with the same interface as LLMClient.

    Uses boto3's bedrock-runtime client under the hood.
    Runs synchronous boto3 calls in a thread pool to stay async-compatible.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        region: Optional[str] = None,
        max_retries: int = 3,
    ):
        from app.config import settings

        self.model = model or settings.BEDROCK_MODEL
        self.region = region or settings.AWS_REGION
        self.max_retries = max_retries
        self._client = None  # lazy init

    def _get_client(self):
        """Lazy-init boto3 bedrock-runtime client."""
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
            )
        return self._client

    async def call_llm(
        self,
        messages: List[Dict],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Call Amazon Bedrock with the same interface as LLMClient.call_llm().

        Converts OpenAI-format messages/tools to Bedrock Converse API format,
        then converts the response back to OpenAI format so the rest of the
        codebase needs zero changes.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._call_sync,
            messages, temperature, max_tokens, tools, tool_choice
        )

    def _call_sync(
        self,
        messages: List[Dict],
        temperature: float,
        max_tokens: Optional[int],
        tools: Optional[List[Dict]],
        tool_choice: Optional[str],
    ) -> Dict[str, Any]:
        """Synchronous Bedrock call — runs in thread pool."""
        client = self._get_client()

        # Separate system message from conversation messages
        system_content = []
        converse_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "system":
                system_content.append({"text": content})
            elif role == "user":
                converse_messages.append({
                    "role": "user",
                    "content": self._convert_content(content)
                })
            elif role == "assistant":
                # Handle tool calls in assistant messages
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    content_blocks = []
                    if content:
                        content_blocks.append({"text": content})
                    for tc in tool_calls:
                        content_blocks.append({
                            "toolUse": {
                                "toolUseId": tc["id"],
                                "name": tc["function"]["name"],
                                "input": json.loads(tc["function"]["arguments"])
                            }
                        })
                    converse_messages.append({
                        "role": "assistant",
                        "content": content_blocks
                    })
                else:
                    converse_messages.append({
                        "role": "assistant",
                        "content": [{"text": content or ""}]
                    })
            elif role == "tool":
                # Tool result — must follow the assistant tool_use message
                converse_messages.append({
                    "role": "user",
                    "content": [{
                        "toolResult": {
                            "toolUseId": msg.get("tool_call_id", ""),
                            "content": [{"text": str(content)}]
                        }
                    }]
                })

        # Build inference config
        inference_config = {"temperature": temperature}
        if max_tokens:
            inference_config["maxTokens"] = max_tokens

        # Build tool config
        tool_config = None
        if tools:
            tool_config = {
                "tools": [self._convert_tool(t) for t in tools]
            }
            if tool_choice == "auto":
                tool_config["toolChoice"] = {"auto": {}}

        # Call Bedrock Converse API
        kwargs = {
            "modelId": self.model,
            "messages": converse_messages,
            "inferenceConfig": inference_config,
        }
        if system_content:
            kwargs["system"] = system_content
        if tool_config:
            kwargs["toolConfig"] = tool_config

        for attempt in range(self.max_retries + 1):
            try:
                response = client.converse(**kwargs)
                return self._convert_response(response)
            except Exception as e:
                if attempt < self.max_retries:
                    import time
                    time.sleep(2 ** attempt)
                    logger.warning(f"Bedrock retry {attempt + 1}: {e}")
                else:
                    raise

    def _convert_content(self, content) -> List[Dict]:
        """Convert OpenAI content (str or list) to Bedrock content blocks."""
        if isinstance(content, str):
            return [{"text": content}]
        if isinstance(content, list):
            blocks = []
            for item in content:
                if item.get("type") == "text":
                    blocks.append({"text": item["text"]})
                elif item.get("type") == "image_url":
                    # Base64 image
                    url = item["image_url"]["url"]
                    if url.startswith("data:image/"):
                        media_type, b64 = url.split(",", 1)
                        media_type = media_type.split(":")[1].split(";")[0]
                        import base64
                        blocks.append({
                            "image": {
                                "format": media_type.split("/")[1],
                                "source": {
                                    "bytes": base64.b64decode(b64)
                                }
                            }
                        })
            return blocks
        return [{"text": str(content)}]

    def _convert_tool(self, tool: Dict) -> Dict:
        """Convert OpenAI tool format to Bedrock tool format."""
        fn = tool["function"]
        return {
            "toolSpec": {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": {
                    "json": fn.get("parameters", {"type": "object", "properties": {}})
                }
            }
        }

    def _convert_response(self, response: Dict) -> Dict:
        """Convert Bedrock Converse response to OpenAI response format."""
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks = message.get("content", [])
        stop_reason = response.get("stopReason", "end_turn")

        # Extract text and tool use blocks
        text_parts = []
        tool_calls = []

        for block in content_blocks:
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append({
                    "id": tu["toolUseId"],
                    "type": "function",
                    "function": {
                        "name": tu["name"],
                        "arguments": json.dumps(tu["input"])
                    }
                })

        text = " ".join(text_parts) if text_parts else ""

        # Strip Nova chain-of-thought tags so the agent layer is model-agnostic
        import re
        text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL)
        text = re.sub(r'<response>(.*?)</response>', r'\1', text, flags=re.DOTALL)
        text = re.sub(r'<answer>(.*?)</answer>', r'\1', text, flags=re.DOTALL)
        text = text.strip()

        # Map Bedrock stop reasons to OpenAI finish reasons
        finish_reason_map = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }
        finish_reason = finish_reason_map.get(stop_reason, "stop")

        assistant_message = {"role": "assistant", "content": text}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls

        return {
            "choices": [{
                "message": assistant_message,
                "finish_reason": finish_reason,
            }],
            "model": self.model,
        }

    async def close(self):
        """No persistent connection to close for boto3."""
        pass
