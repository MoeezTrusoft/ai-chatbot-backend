import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel

from bookcraft.components.llm.metrics import LLM_CALLS, LLM_LATENCY

# Output-token ceiling per structured call. Measured: a reply that answers a
# 13-question checklist is ~1030 output tokens by itself, so the previous
# hardcoded 1024 truncated it even with thinking off (chat 5876). A ceiling is
# billed on tokens actually produced, so the headroom costs nothing.
DEFAULT_MAX_TOKENS = 2048

# Accepted `thinking` modes. "disabled" and "adaptive" are both valid on
# claude-sonnet-5, claude-sonnet-4-6 and claude-haiku-4-5 (all verified against
# the live API). The {"type": "enabled", "budget_tokens": N} form is NOT — the
# API rejects it on claude-sonnet-5 with:
#   "thinking.type.enabled" is not supported for this model.
# Depth under "adaptive" is controlled by output_config.effort, not a token budget.
_THINKING_MODES = frozenset({"disabled", "adaptive"})


def _thinking_payload(mode: str) -> dict[str, str] | None:
    """Build the request's `thinking` field, or None to send no field at all.

    Sending nothing is NOT the same as disabling: claude-sonnet-5 treats an absent
    `thinking` as adaptive, which is the trap this function exists to make explicit.
    """
    normalized = (mode or "").strip().lower()
    if normalized == "omit":
        return None
    if normalized not in _THINKING_MODES:
        raise ValueError(
            f"unsupported thinking mode {mode!r}; expected one of "
            f"{sorted(_THINKING_MODES)} or 'omit'"
        )
    return {"type": normalized}


# ---------------------------------------------------------------------------
# Shared HTTP client — created once, reused across all LLM calls.
# Using HTTP/2 and a persistent connection pool avoids a TCP+TLS handshake
# (typically 100-300 ms) on every request.
# ---------------------------------------------------------------------------

_shared_client: httpx.AsyncClient | None = None


async def get_shared_client(
    *,
    read_timeout: float | None = None,
) -> httpx.AsyncClient:
    """Return the process-wide shared AsyncClient, creating it on first call.

    The *read_timeout* argument is only used on the very first call (when the
    client is being initialised); subsequent calls return the cached instance
    regardless of the argument value.  Pass the most generous timeout you need
    (i.e. the generation timeout) so that long responses are never cut off.
    """
    global _shared_client  # noqa: PLW0603
    if _shared_client is None:
        timeout = httpx.Timeout(
            connect=10.0,
            read=read_timeout,  # None = unbounded when feature is disabled
            write=30.0,
            pool=5.0,
        )
        _shared_client = httpx.AsyncClient(
            http2=True,
            timeout=timeout,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=100,
            ),
        )
    return _shared_client


async def close_shared_client() -> None:
    """Gracefully close the shared client during application shutdown."""
    global _shared_client  # noqa: PLW0603
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


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
        system_cache_suffix: str | None = None,
    ) -> BaseModel:
        del system, user, system_cache_suffix
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            return output_model.model_validate({})


def _anthropic_system_payload(
    system: str, suffix: str | None, cache_enabled: bool
) -> object:
    """Build the Anthropic ``system`` field.

    When prompt caching is on, the stable ``system`` text is emitted as a single
    ``cache_control`` block (the cached prefix), and any volatile ``suffix``
    (e.g. the current date/time) is appended as a SEPARATE, uncached block so it
    never invalidates the cached prefix.  When caching is off, the two are simply
    concatenated so the model still sees identical content.

    Why only ONE cache breakpoint (advisory item #3, deliberately NOT expanded):
    Anthropic allows up to 4 ``cache_control`` breakpoints, but caching matches a
    prefix from byte 0, so a second breakpoint only pays off when an *invariant*
    span precedes the variable one. Neither prompt satisfies that here:
      * The user message (``messages[0].content``) is volatile from its first
        byte — it opens with the author's current message — so it has no stable
        prefix to cache.
      * The system prompt's large invariant policy body (contact info,
        consultation flow, scope, output protocol) is preceded by a per-turn /
        per-thread VARIABLE head (persona identity + service-specific style, see
        ``generator._response_system_prompt``). Turn-to-turn within a thread the
        whole system text is byte-stable, so the single breakpoint already
        captures that hit; sharing the invariant body across threads would
        require reordering it ahead of the variable head, which changes the exact
        bytes the model sees and is out of scope for a caching-only change.
    So the only content outside the cached block is the volatile date/time suffix
    — a contract locked by ``tests/unit/test_adapter_prompt_cache.py``.
    """
    if cache_enabled:
        blocks: list[dict[str, object]] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        if suffix:
            blocks.append({"type": "text", "text": suffix})
        return blocks
    if suffix:
        return f"{system}\n{suffix}"
    return system


@dataclass(slots=True)
class AnthropicAdapter:
    api_key: str
    base_url: str
    timeout_seconds: float  # kept for config compatibility
    model: str = "claude-haiku-4-5-20251001"
    name: str = "anthropic"
    # None = unbounded read (current default; safe for long LLM responses)
    read_timeout: float | None = field(default=None)
    prompt_cache_enabled: bool = field(default=False)
    # Cap on output tokens per call. This is a ceiling, not a reservation — billing
    # is on tokens actually generated — so headroom is free. 1024 was not enough:
    # a reply answering a dozen questions measures ~1030 output tokens on its own.
    max_tokens: int = field(default=DEFAULT_MAX_TOKENS)
    # Sent verbatim as the request's `thinking` field. Explicit by necessity: on
    # claude-sonnet-4-6 an omitted `thinking` meant NO thinking, but on
    # claude-sonnet-5 the same omission silently runs ADAPTIVE thinking. Since
    # max_tokens covers thinking + text combined, a complex prompt spent the whole
    # budget reasoning, returned stop_reason=max_tokens with no text block at all,
    # and every response failed schema validation and fell back to a canned
    # template (chat 5876). Never use {"type": "enabled", "budget_tokens": N} —
    # claude-sonnet-5 rejects it with a 400.
    thinking_mode: str = field(default="disabled")

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
        system_cache_suffix: str | None = None,
        max_tokens: int | None = None,
    ) -> BaseModel:
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            client = await get_shared_client(read_timeout=self.read_timeout)

            headers: dict[str, str] = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            if self.prompt_cache_enabled:
                headers["anthropic-beta"] = "prompt-caching-2024-07-31"

            # Stable system text is cached; the volatile suffix (date/time) is a
            # separate, uncached block so it never breaks the cached prefix.
            system_payload = _anthropic_system_payload(
                system, system_cache_suffix, self.prompt_cache_enabled
            )

            # Build a per-call timeout that respects the instance read_timeout.
            # The shared client has its own default timeout; we pass an explicit
            # one here so adapters with different read budgets share the same
            # underlying connection pool while still honouring their limits.
            call_timeout = httpx.Timeout(
                connect=10.0,
                read=self.read_timeout,
                write=30.0,
                pool=5.0,
            )

            body: dict[str, object] = {
                "model": self.model,
                "max_tokens": max_tokens or self.max_tokens,
                "system": system_payload,
                "messages": [{"role": "user", "content": user}],
            }
            thinking = _thinking_payload(self.thinking_mode)
            if thinking is not None:
                body["thinking"] = thinking

            response = await client.post(
                f"{self.base_url.rstrip('/')}/v1/messages",
                headers=headers,
                json=body,
                timeout=call_timeout,
            )
            response.raise_for_status()
        return _parse_structured_response(response.text, output_model)

    async def stream_text(
        self,
        *,
        system: str,
        messages: list[dict[str, object]],
        max_tokens: int = 1024,
        purpose: str = "response_stream",
        system_cache_suffix: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream plain text deltas from the Anthropic Messages streaming API.

        Issues a POST to ``/v1/messages`` with ``"stream": true`` and parses the
        documented SSE event stream, yielding ``delta.text`` from each
        ``content_block_delta`` event whose delta is a ``text_delta``.  Built in
        the exact style of :meth:`structured` (same headers, base_url, model, and
        prompt-cache handling) but over a streaming connection.

        Defensive by design: any transport, HTTP-status, or SSE ``error`` event
        raises so the caller (the generator) can fall back to a non-streaming
        path.  This method must not be called when the adapter has no API key /
        is in mock mode — the generator decides whether streaming is viable.
        """
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        client = await get_shared_client(read_timeout=self.read_timeout)

        headers: dict[str, str] = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        if self.prompt_cache_enabled:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"

        # Mirrors structured() so a streaming call shares the same cached prefix:
        # stable system text cached, volatile suffix (date/time) uncached.
        system_payload = _anthropic_system_payload(
            system, system_cache_suffix, self.prompt_cache_enabled
        )

        call_timeout = httpx.Timeout(
            connect=10.0,
            read=self.read_timeout,
            write=30.0,
            pool=5.0,
        )

        async with client.stream(
            "POST",
            f"{self.base_url.rstrip('/')}/v1/messages",
            headers=headers,
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system_payload,
                "messages": messages,
                "stream": True,
            },
            timeout=call_timeout,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                text = _sse_text_delta(line)
                if text:
                    yield text


@dataclass(slots=True)
class OpenAIAdapter:
    api_key: str
    base_url: str
    timeout_seconds: float
    model: str = "gpt-5.4-mini"
    name: str = "openai"
    # None = unbounded read (current default)
    read_timeout: float | None = field(default=None)

    async def structured(
        self,
        *,
        system: str,
        user: str,
        output_model: type[BaseModel],
        purpose: str,
        system_cache_suffix: str | None = None,
    ) -> BaseModel:
        # OpenAI-style APIs have no prompt-cache block; fold any suffix into the
        # system message so the model still sees identical content.
        if system_cache_suffix:
            system = f"{system}\n{system_cache_suffix}"
        LLM_CALLS.labels(provider=self.name, purpose=purpose).inc()
        with LLM_LATENCY.labels(provider=self.name, purpose=purpose).time():
            client = await get_shared_client(read_timeout=self.read_timeout)

            call_timeout = httpx.Timeout(
                connect=10.0,
                read=self.read_timeout,
                write=30.0,
                pool=5.0,
            )

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
                payload["max_completion_tokens"] = 320
            else:
                payload["max_tokens"] = 320

            response = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=call_timeout,
            )
            response.raise_for_status()
        return _parse_structured_response(response.text, output_model)


@dataclass(slots=True)
class DeepSeekAdapter(OpenAIAdapter):
    name: str = "deepseek"


def _sse_text_delta(line: str) -> str | None:
    """Extract incremental text from a single Anthropic SSE stream line.

    The Messages streaming API emits Server-Sent Events. Each event is a pair of
    lines: ``event: <type>`` then ``data: <json>``.  We only care about the
    ``data:`` payloads here; ``content_block_delta`` events whose delta is a
    ``text_delta`` carry the incremental text in ``delta.text``.  Anything else
    (``message_start``, ``content_block_start``, ``ping``, ``message_stop``,
    blank keep-alive lines) yields no text.

    An SSE ``error`` event (``data: {"type": "error", ...}``) is raised so the
    caller can fall back — a mid-stream API error must never look like a normal
    end-of-turn.
    """
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        event = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if event_type == "error":
        error = event.get("error")
        message = (
            error.get("message")
            if isinstance(error, dict)
            else "anthropic stream error"
        )
        raise RuntimeError(f"anthropic_stream_error: {message}")
    if event_type != "content_block_delta":
        return None
    delta = event.get("delta")
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return text if isinstance(text, str) and text else None


def _parse_structured_response(raw: str, output_model: type[BaseModel]) -> BaseModel:
    payload = json.loads(raw)
    if isinstance(payload, dict) and "content" in payload:
        content = payload["content"]
        if isinstance(content, list) and content:
            # Select the first block that actually carries text. With extended
            # thinking, content[0] is a {"type": "thinking", ...} block (no "text"
            # key), so assuming index 0 fails to parse and the raw envelope leaks
            # into model_validate. Scan for the text block instead (chat 6688 run:
            # claude-sonnet-5 emitted thinking blocks and every turn fell to retry).
            text_block = next(
                (
                    block
                    for block in content
                    if isinstance(block, dict) and isinstance(block.get("text"), str)
                ),
                None,
            )
            if text_block is not None:
                payload = _loads_json_object(text_block["text"])
            elif payload.get("stop_reason") == "max_tokens":
                # The whole output budget was consumed before any text was emitted
                # — with thinking on, that means the model reasoned until it ran
                # out. Say so: this previously surfaced as ~10 pydantic "extra
                # inputs are not permitted" errors on the raw envelope, which
                # pointed nowhere near the real cause (chat 5876).
                kinds = sorted(
                    {b.get("type") for b in content if isinstance(b, dict) and b.get("type")}
                )
                raise ValueError(
                    "model hit max_tokens before producing any text block "
                    f"(content blocks: {kinds or 'none'}). The output budget is "
                    "shared between thinking and the reply — raise max_tokens or "
                    "set thinking_mode='disabled'."
                )
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
        fragment = stripped[match.start() :]
        try:
            obj, _ = decoder.raw_decode(fragment)
            return obj
        except json.JSONDecodeError as exc:
            last_error = exc

        # Best-effort repair for models that return a JSON object cut off near the end.
        if fragment.startswith("{"):
            repaired = fragment
            open_braces = repaired.count("{") - repaired.count("}")
            open_brackets = repaired.count("[") - repaired.count("]")
            if open_brackets > 0:
                repaired += "]" * open_brackets
            if open_braces > 0:
                repaired += "}" * open_braces
            try:
                obj, _ = decoder.raw_decode(repaired)
                return obj
            except json.JSONDecodeError as repair_exc:
                last_error = repair_exc

    preview = stripped[:240].replace("\\n", " ")
    message = f"could not parse JSON object from model content preview={preview!r}"
    raise json.JSONDecodeError(message, stripped, 0) from last_error
