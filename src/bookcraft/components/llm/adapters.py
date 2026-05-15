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
                payload: dict[str, object] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "stream": False,
                }
                if self.name.startswith("openai"):
                    payload["max_completion_tokens"] = 160
                else:
                    payload["max_tokens"] = 160

                response = await client.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
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
    if not stripped:
        raise json.JSONDecodeError("empty model content", stripped, 0)

    candidates = [stripped]

    fenced = re.search(r"```(?:json)?\\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None

    for match in re.finditer(r"[{\\[]", stripped):
        try:
            obj, _ = decoder.raw_decode(stripped[match.start():])
            return obj
        except json.JSONDecodeError as exc:
            last_error = exc

    preview = stripped[:240].replace("\\n", " ")
    message = f"could not parse JSON object from model content preview={preview!r}"
    raise json.JSONDecodeError(message, stripped, 0) from last_error

