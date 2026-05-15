import json
import re
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from bookcraft.components.llm.metrics import LLM_CALLS, LLM_LATENCY


@dataclass(slots=True)
class MockLLMAdapter:
    name: str = "mock"

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
    ) -> BaseModel:
        del system, user
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            return output_model.model_validate({})


@dataclass(slots=True)
class AnthropicAdapter:
    api_key: str
    base_url: str
    timeout_seconds: float
    model: str = "claude-haiku-4-5"
    name: str = "anthropic"

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
    ) -> BaseModel:
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/v1/messages",
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": self.model,
                        "max_tokens": 1024,
                        "system": system,
                        "messages": [{"role": "user", "content": user}],
                    },
                )
                response.raise_for_status()
        return _parse_structured_response(response.text, output_model)


@dataclass(slots=True)
class OpenAIAdapter:
    api_key: str
    base_url: str
    timeout_seconds: float
    model: str = "gpt-5.4-mini"
    name: str = "openai"

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
    ) -> BaseModel:
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 160,
                        "stream": False,
                    },
                )
                response.raise_for_status()
        return _parse_structured_response(response.text, output_model)


@dataclass(slots=True)
class DeepSeekAdapter(OpenAIAdapter):
    name: str = "deepseek"


def _parse_structured_response(raw: str, output_model: type[BaseModel]) -> BaseModel:
    payload = json.loads(raw)
    if isinstance(payload, dict) and "content" in payload:
        content = payload["content"]
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and isinstance(first.get("text"), str):
                payload = _loads_json_object(first["text"])
    if isinstance(payload, dict) and "choices" in payload:
        content = payload["choices"][0]["message"]["content"]
        payload = _loads_json_object(content)
    return output_model.model_validate(payload)


def _loads_json_object(text: str) -> object:
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        decode_error = exc
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.S)
    if match is None:
        raise decode_error
    return json.loads(match.group(0))
