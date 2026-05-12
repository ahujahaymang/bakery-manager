"""
Agent Service — router.

Selects the LLM backend based on AGENT_BACKEND setting:
  "bedrock"  — Amazon Bedrock Nova Lite (default, cheap)
  "gpt"      — OpenAI GPT-4o mini (fallback if Bedrock is too slow)

To switch backend, set AGENT_BACKEND=gpt in .env (or SSM Parameter Store).
No code changes needed.
"""

from app.config import settings

if settings.AGENT_BACKEND == "gpt":
    from app.services.agent_service_gpt import AgentService, TOOLS, SYSTEM_PROMPT  # noqa: F401
else:
    from app.services.agent_service_bedrock import AgentService, TOOLS, SYSTEM_PROMPT  # noqa: F401

__all__ = ["AgentService", "TOOLS", "SYSTEM_PROMPT"]
