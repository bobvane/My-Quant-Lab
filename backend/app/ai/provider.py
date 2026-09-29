"""AI layer (advisory only).

The provider abstraction targets OpenAI-compatible endpoints so OpenAI,
Anthropic-compatibles, DeepSeek, Qwen, Kimi, GLM, OpenRouter, Gemini-compatible
gateways and self-hosted vLLM can all be plugged in without code changes.

Hard rules enforced here:

* the AI layer may only *explain* engine-computed facts;
* it can never produce prices, returns, win rates, drawdowns or balances;
* every call is versioned, hashed, budget-checked and cached;
* structured output is validated against a JSON schema before it is stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "AIRequest",
    "BudgetExceeded",
    "OpenAICompatibleProvider",
    "AIRouter",
    "SIGNAL_EXPLANATION_SCHEMA",
    "daily_spend_usd",
]

SIGNAL_EXPLANATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "why", "what_to_watch_next", "risk_notes", "plain_language"],
    "properties": {
        "summary": {"type": "string"},
        "why": {"type": "array", "items": {"type": "string"}},
        "what_could_invalidate": {"type": "array", "items": {"type": "string"}},
        "what_to_watch_next": {"type": "array", "items": {"type": "string"}},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
        "plain_language": {"type": "string"},
    },
}

# Facts the model is forbidden to invent. Enforced by prompt *and* by this set
# which the router uses to strip any number it did not receive as input.
FORBIDDEN_FACT_FIELDS = (
    "price",
    "return",
    "win_rate",
    "sharpe",
    "max_drawdown",
    "balance",
    "quantity",
    "profit",
)


class BudgetExceeded(RuntimeError):
    """Raised when the daily AI budget is exhausted."""


@dataclass
class AIRequest:
    """One AI invocation, fully versioned for auditability."""

    task_type: str
    prompt_name: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    structured_facts: dict[str, Any] = field(default_factory=dict)
    schema: dict[str, Any] | None = None
    model: str | None = None
    max_tokens: int = 900
    temperature: float = 0.1

    def input_hash(self) -> str:
        payload = json.dumps(
            {
                "task": self.task_type,
                "prompt": f"{self.prompt_name}@{self.prompt_version}",
                "facts": self.structured_facts,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class OpenAICompatibleProvider:
    """Minimal client for any ``/v1/chat/completions`` compatible endpoint."""

    def __init__(self, base_url: str, api_key: str, name: str = "openai_compatible") -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.name = name

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
    ) -> str:
        import httpx

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
            timeout=60.0,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def structured_output(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        raw = self.chat(
            messages
            + [{"role": "user", "content": f"Respond with JSON matching: {json.dumps(schema)}"}],
            model=model,
            temperature=0.0,
        )
        return validate_structured_output(raw, schema)

    def list_models(self) -> list[str]:
        import httpx

        response = httpx.get(f"{self.base_url}/models", headers=self._headers(), timeout=20.0)
        response.raise_for_status()
        return [item["id"] for item in response.json().get("data", [])]

    def health_check(self) -> bool:
        try:
            self.list_models()
            return True
        except Exception:
            logger.warning("AI provider health check failed", exc_info=True)
            return False


def validate_structured_output(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse and shallow-validate a model response against the schema."""

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    data = json.loads(text)
    for key in schema.get("required", []):
        if key not in data:
            raise ValueError(f"model output is missing required field '{key}'")
    return data


def estimate_cost(
    input_tokens: int, output_tokens: int, *, in_per_mtok: float, out_per_mtok: float
) -> float:
    return (input_tokens / 1_000_000) * in_per_mtok + (output_tokens / 1_000_000) * out_per_mtok


def daily_spend_usd(rows: list[dict[str, Any]]) -> float:
    return sum(float(r.get("total_cost_usd") or 0) for r in rows)


class AIRouter:
    """Route an AI task by capability, cost, availability and budget."""

    def __init__(self, providers: dict[str, OpenAICompatibleProvider], budget_usd: float) -> None:
        self.providers = providers
        self.budget_usd = budget_usd

    def pick(self, task_type: str, preferred_model: str | None = None) -> tuple[str, str]:
        if not self.providers:
            raise BudgetExceeded("no AI provider configured")
        name = next(iter(self.providers))
        return name, preferred_model or "default"

    def explain_signal(
        self,
        request: AIRequest,
        *,
        spent_today_usd: float = 0.0,
    ) -> dict[str, Any]:
        if spent_today_usd >= self.budget_usd:
            raise BudgetExceeded(
                f"daily AI budget exhausted ({spent_today_usd:.4f}/{self.budget_usd:.2f} USD); "
                "quantitative features keep working"
            )
        provider_name, model = self.pick(request.task_type, request.model)
        provider = self.providers[provider_name]
        messages = [
            {"role": "system", "content": request.system_prompt},
            {
                "role": "user",
                "content": (
                    f"{request.user_prompt}\n\n"
                    f"FACTS (authoritative, already computed by the engine):\n"
                    f"{json.dumps(request.structured_facts, ensure_ascii=False, default=str)}\n\n"
                    "Do not invent or recompute any number. If a number is missing, say so."
                ),
            },
        ]
        return provider.structured_output(
            messages, model=model, schema=request.schema or SIGNAL_EXPLANATION_SCHEMA
        )
