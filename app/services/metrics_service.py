"""
MetricsService — lightweight in-process observability.

Tracks the metrics that matter most for a small SaaS bot:
  - LLM calls (count, token usage, cost, latency) per tenant
  - Error counts by type
  - Active users (unique tenants seen in last 24h / 7d)
  - Tool call counts (which tools are most used)
  - Request latency distribution

All metrics are kept in-memory (per process restart). The /metrics API
endpoint exposes a JSON summary. For persistence, CloudWatch Logs already
capture structured log lines — grep for "METRIC" to build dashboards.

Costs are estimated using published OpenAI / Bedrock pricing and updated
in the COST_PER_1K_TOKENS dict below.
"""

import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Pricing (USD per 1k tokens) ────────────────────────────────────────────────
# Update these when pricing changes.
COST_PER_1K_TOKENS: Dict[str, Dict[str, float]] = {
    # model_id: {"input": $/1k, "output": $/1k}
    "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
    "gpt-4.1-nano": {"input": 0.00010, "output": 0.00040},
    "gpt-4o": {"input": 0.00250, "output": 0.01000},
    # Bedrock Nova Lite (on-demand, us-east-1)
    "amazon.nova-lite-v1:0": {"input": 0.00006, "output": 0.00024},
    # Bedrock Nova Micro
    "amazon.nova-micro-v1:0": {"input": 0.000035, "output": 0.00014},
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for a single LLM call."""
    pricing = COST_PER_1K_TOKENS.get(model)
    if not pricing:
        return 0.0
    return (input_tokens / 1000) * pricing["input"] + (output_tokens / 1000) * pricing["output"]


class MetricsService:
    """
    Thread-safe in-memory metrics accumulator.

    Instantiate once as a module-level singleton (see bottom of file).
    All methods are safe to call from concurrent async/threaded contexts.
    """

    def __init__(self, retention_hours: int = 24):
        self._lock = Lock()
        self._retention = timedelta(hours=retention_hours)

        # ── LLM call log (rolling window) ─────────────────────────────────
        # Each entry: {ts, tenant_id, model, input_tokens, output_tokens, latency_ms, cost_usd, success}
        self._llm_calls: deque = deque(maxlen=10_000)

        # ── Error log (rolling window) ─────────────────────────────────────
        # Each entry: {ts, tenant_id, error_type, message}
        self._errors: deque = deque(maxlen=5_000)

        # ── Request log (rolling window) ──────────────────────────────────
        # Each entry: {ts, tenant_id, latency_ms}
        self._requests: deque = deque(maxlen=10_000)

        # ── Tool call counts (all time, resets on restart) ─────────────────
        self._tool_counts: Dict[str, int] = defaultdict(int)

        # ── Active tenant sets ─────────────────────────────────────────────
        # Deque of (ts, tenant_id) pairs, trimmed on read
        self._activity: deque = deque(maxlen=50_000)

        self._started_at = datetime.utcnow()

    # ── Record methods ─────────────────────────────────────────────────────

    def record_llm_call(
        self,
        *,
        tenant_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        success: bool = True,
        call_type: str = "agent",  # "agent" | "image" | "intent"
    ) -> None:
        """Record one LLM API call. Call this after every model invocation."""
        cost = _estimate_cost(model, input_tokens, output_tokens)
        entry = {
            "ts": time.time(),
            "tenant_id": tenant_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost_usd": cost,
            "success": success,
            "call_type": call_type,
        }
        with self._lock:
            self._llm_calls.append(entry)

        # Emit a structured log line for CloudWatch metric filters
        logger.info(
            "METRIC llm_call tenant=%s model=%s input_tok=%d output_tok=%d "
            "latency_ms=%.0f cost_usd=%.6f success=%s type=%s",
            tenant_id, model, input_tokens, output_tokens,
            latency_ms, cost, success, call_type,
        )

    def record_error(
        self,
        *,
        tenant_id: str,
        error_type: str,
        message: str,
    ) -> None:
        """Record an application error."""
        entry = {
            "ts": time.time(),
            "tenant_id": tenant_id,
            "error_type": error_type,
            "message": message[:500],
        }
        with self._lock:
            self._errors.append(entry)

        logger.info(
            "METRIC error tenant=%s type=%s",
            tenant_id, error_type,
        )

    def record_request(
        self,
        *,
        tenant_id: str,
        latency_ms: float,
    ) -> None:
        """Record one end-to-end user request (from message received to reply sent)."""
        entry = {"ts": time.time(), "tenant_id": tenant_id, "latency_ms": latency_ms}
        with self._lock:
            self._requests.append(entry)
            self._activity.append((time.time(), tenant_id))

        logger.info(
            "METRIC request tenant=%s latency_ms=%.0f",
            tenant_id, latency_ms,
        )

    def record_tool_call(self, tool_name: str) -> None:
        """Increment the counter for a tool invocation."""
        with self._lock:
            self._tool_counts[tool_name] += 1

    # ── Summary ────────────────────────────────────────────────────────────

    def summary(self, window_hours: int = 24) -> Dict[str, Any]:
        """
        Return a JSON-serialisable metrics summary for the given rolling window.

        Called by the /metrics API endpoint.
        """
        cutoff = time.time() - window_hours * 3600
        now_dt = datetime.utcnow()

        with self._lock:
            llm = [e for e in self._llm_calls if e["ts"] >= cutoff]
            errs = [e for e in self._errors if e["ts"] >= cutoff]
            reqs = [e for e in self._requests if e["ts"] >= cutoff]
            activity = [(ts, tid) for ts, tid in self._activity if ts >= cutoff]
            tool_counts = dict(self._tool_counts)

        # ── LLM stats ──────────────────────────────────────────────────────
        total_calls = len(llm)
        failed_calls = sum(1 for e in llm if not e["success"])
        total_input_tokens = sum(e["input_tokens"] for e in llm)
        total_output_tokens = sum(e["output_tokens"] for e in llm)
        total_cost = sum(e["cost_usd"] for e in llm)
        avg_latency = (sum(e["latency_ms"] for e in llm) / total_calls) if total_calls else 0

        # Cost per model
        cost_by_model: Dict[str, float] = defaultdict(float)
        calls_by_model: Dict[str, int] = defaultdict(int)
        for e in llm:
            cost_by_model[e["model"]] += e["cost_usd"]
            calls_by_model[e["model"]] += 1

        # ── Active users ───────────────────────────────────────────────────
        active_tenants_24h = len({tid for ts, tid in activity if ts >= time.time() - 86400})
        active_tenants_7d = len({tid for ts, tid in self._activity if ts >= time.time() - 7 * 86400})

        # ── Request latency percentiles ───────────────────────────────────
        latencies = sorted(e["latency_ms"] for e in reqs)
        p50 = latencies[len(latencies) // 2] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

        # ── Error breakdown ────────────────────────────────────────────────
        error_by_type: Dict[str, int] = defaultdict(int)
        for e in errs:
            error_by_type[e["error_type"]] += 1

        # ── Cost per active tenant ─────────────────────────────────────────
        active_count = active_tenants_24h or 1
        cost_per_user = total_cost / active_count

        # ── Top tools ─────────────────────────────────────────────────────
        top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:15]

        return {
            "window_hours": window_hours,
            "generated_at": now_dt.isoformat() + "Z",
            "uptime_hours": round((now_dt - self._started_at).total_seconds() / 3600, 1),

            "users": {
                "active_24h": active_tenants_24h,
                "active_7d": active_tenants_7d,
            },

            "requests": {
                "total": len(reqs),
                "latency_p50_ms": round(p50),
                "latency_p95_ms": round(p95),
                "latency_p99_ms": round(p99),
            },

            "llm": {
                "total_calls": total_calls,
                "failed_calls": failed_calls,
                "failure_rate_pct": round(failed_calls / total_calls * 100, 1) if total_calls else 0,
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cost_usd": round(total_cost, 4),
                "cost_per_active_user_usd": round(cost_per_user, 4),
                "avg_latency_ms": round(avg_latency),
                "by_model": {
                    model: {
                        "calls": calls_by_model[model],
                        "cost_usd": round(cost_by_model[model], 4),
                    }
                    for model in cost_by_model
                },
            },

            "errors": {
                "total": len(errs),
                "by_type": dict(error_by_type),
            },

            "tools": {
                "top": [{"name": t, "calls": c} for t, c in top_tools],
            },
        }


# ── Module-level singleton ─────────────────────────────────────────────────────
# Import from here: `from app.services.metrics_service import metrics`
metrics = MetricsService()
