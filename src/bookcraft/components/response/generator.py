import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo

import structlog
from prometheus_client import Histogram

from bookcraft.components.context.schemas import ContextPack
from bookcraft.components.extraction.schemas import CombinedExtraction
from bookcraft.components.intent.schemas import IntentVote
from bookcraft.components.llm.metrics import LLM_CALLS
from bookcraft.components.llm.protocols import LLMProvider
from bookcraft.components.portfolio.schemas import PortfolioResponse, PortfolioStatus
from bookcraft.components.preprocessor.schemas import ProcessedMessage
from bookcraft.components.pricing.models import PricingTimelineQuote, QuoteStatus
from bookcraft.components.rag.schemas import RetrievedChunk
from bookcraft.components.response.planner import ResponsePlan
from bookcraft.components.response.quality_gate import ResponseQualityReport
from bookcraft.components.response.routing import ResponseRouter
from bookcraft.components.response.schemas import GeneratedResponseText, ResponseDraft
from bookcraft.components.response.style_policy import ResponseStylePolicy
from bookcraft.components.tools.governance import ToolGovernanceDecision
from bookcraft.domain.enums import QueryIntentType, Source
from bookcraft.domain.state import ThreadState

RESPONSE_SECONDS = Histogram("response_generation_seconds", "Response generation latency.")

GREETING_RESPONSE = "Hello! How can I help with your book project today?"
logger = structlog.get_logger(__name__)

# Module-level style policy used to build the LLM system prompt.
_STYLE_POLICY = ResponseStylePolicy.default()

# How many retrieved chunks reach the prompt. A normal turn answers one thing and a
# tight pack keeps it focused; a multi-question turn has to carry grounding for every
# question asked, so it gets a wider budget (chat 5876).
_SINGLE_QUESTION_RAG_CHUNKS = 5
_MULTI_QUESTION_RAG_CHUNKS = 12


@dataclass(slots=True)
class SonnetResponseGenerator:
    provider_name: str = "mock_sonnet"
    adapter: LLMProvider | None = None
    # Secondary Claude model (e.g. Sonnet) tried when the primary (e.g. Opus) fails
    # every attempt — usually HTTP 529 Overloaded. Answering with another Claude model
    # keeps the reply Claude-generated instead of dropping to the canned template.
    fallback_adapter: LLMProvider | None = None
    router: ResponseRouter = field(default_factory=ResponseRouter)
    # Number of no-RAG re-initiations tried after the full (with-RAG) attempt
    # before conceding to the safe template. The model is stochastic, so re-issuing
    # the request usually clears a transient transport error or a one-off validation
    # rejection — far better than emitting a canned fallback that may read as
    # off-context (chat 6688's "bestseller" template). The first re-initiation keeps
    # the historical "reduced" label; the rest are "retry" attempts.
    response_retry_attempts: int = 3

    async def generate(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk] | None = None,
        pricing_quote: PricingTimelineQuote | None = None,
        timeline_estimate: PricingTimelineQuote | None = None,
        pricing_missing_question: str | None = None,
        portfolio_response: PortfolioResponse | None = None,
        document_status_message: str | None = None,
        runtime_atoms: dict[str, Any] | None = None,
        response_hint: str | None = None,
        context_pack: ContextPack | None = None,
        response_plan: ResponsePlan | None = None,
        recent_turns: list[tuple[str, str]] | None = None,
        persona_decision: Any | None = None,
    ) -> ResponseDraft:
        with RESPONSE_SECONDS.time():
            route = self.router.route(intent)
            rag_chunks = rag_chunks or []
            runtime_atoms = runtime_atoms or {}

            if (
                intent.query_primary == QueryIntentType.GREETING
                and intent.confidence >= 0.9
                and message.normalized.lower() in {"hi", "hello", "hey"}
                and self.adapter is None
            ):
                return ResponseDraft(text=GREETING_RESPONSE, source="deterministic_greeting")

            # Out-of-scope detection: retail book purchasing is not a BookCraft service.
            # Detect and pass as a grounded fact so Claude writes a natural response.
            # Never hardcode the customer-facing text — Claude writes it using the
            # scope guidance already in the system prompt.
            _out_of_scope_fact: str | None = None
            if _is_retail_book_order(message.raw or message.normalized or ""):
                _out_of_scope_fact = (
                    "OUT-OF-SCOPE DETECTION: This message is asking about retail or wholesale "
                    "book purchasing (bulk copies, ISBNs, tiered discounts, freight quotes). "
                    "BookCraft does not sell retail copies of other publishers' titles. "
                    "Politely explain that BookCraft helps AUTHORS publish their own books, "
                    "not book buyers. Suggest the publisher or a distributor like Ingram. "
                    "Invite them to return if they have a manuscript to publish. "
                    "Do NOT invent any prices, discounts, freight rates, or delivery times."
                )

            # Step 4 (tone fix): when the LLM adapter is present, pass engine outputs as
            # grounded facts and let the LLM write the prose.  When the adapter is absent,
            # keep the deterministic fallback paths — they are the no-adapter dev path only.
            # Seed engine_facts with the out-of-scope detection if it fired.
            _engine_facts: str | None = _out_of_scope_fact
            _engine_approved_urls: list[str] = []

            if pricing_missing_question and self.adapter is None:
                return ResponseDraft(
                    text=_customer_safe_missing_scope_question(
                        pricing_missing_question,
                        intent=intent,
                        runtime_atoms=runtime_atoms,
                    ),
                    source="pricing_engine",
                )
            elif pricing_missing_question:
                _engine_facts = (
                    f"Pricing engine needs more scope to generate an approved estimate. "
                    f"Missing detail: {pricing_missing_question}"
                )

            if pricing_quote is not None and self.adapter is None:
                return ResponseDraft(
                    text=_pricing_quote_text(pricing_quote),
                    source="pricing_engine",
                )
            elif pricing_quote is not None:
                _engine_facts = _pricing_quote_as_facts(pricing_quote)

            if timeline_estimate is not None and self.adapter is None:
                return ResponseDraft(
                    text=_timeline_quote_text(timeline_estimate),
                    source="pricing_engine",
                )
            elif timeline_estimate is not None:
                _engine_facts = _timeline_estimate_as_facts(timeline_estimate)

            # Only use portfolio engine output when the intent was genuinely a portfolio
            # request (≥ 0.75 confidence). "print a sample" / "sample copy" must NOT
            # trigger portfolio samples — those are publishing/formatting service questions.
            _portfolio_genuine = (
                portfolio_response is not None
                and intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST
                and intent.confidence >= 0.85
            )
            if _portfolio_genuine and portfolio_response is not None and self.adapter is None:
                return _portfolio_response_text(portfolio_response)
            elif _portfolio_genuine and portfolio_response is not None:
                _engine_facts, _engine_approved_urls = _portfolio_response_as_facts(
                    portfolio_response
                )

            if document_status_message is not None and self.adapter is None:
                return ResponseDraft(
                    text=_clean_guarded_status_message(
                        document_status_message,
                        intent=intent,
                        runtime_atoms=runtime_atoms,
                    ),
                    source=route.name,
                )
            elif document_status_message is not None:
                _engine_facts = (
                    f"Document status from the action engine: "
                    f"{_clean_customer_text(document_status_message)}"
                )

            # Guarded mixed request: keep legal/link/price safety — no-adapter only.
            if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST and self.adapter is None:
                return ResponseDraft(
                    text=(
                        "I can help with samples, an estimate, and the NDA step without "
                        "guessing or sending anything generic. For samples, I’d match by "
                        "service and genre; for an estimate, I’d need word or page count, "
                        "manuscript stage, and deadline; and for the NDA, I’d need the author "
                        "name, email, phone, and preferred effective date. Which part would "
                        "you like to start with?"
                    ),
                    source="deterministic_mixed_request_guard",
                )

            template_fallback = _humanized_template_response(
                intent=intent,
                state=state,
                message=message,
                runtime_atoms=runtime_atoms,
                rag_chunks=rag_chunks,
                route_name=route.name,
                response_hint=response_hint,
                context_pack=context_pack,
                response_plan=response_plan,
            )

            if self.adapter is None:
                return ResponseDraft(text=template_fallback, source="template_no_adapter")

            # A multi-question turn needs grounding for every question, not just the
            # first few — 5 chunks cannot cover a dozen distinct questions (chat 5876).
            _rag_budget = (
                _MULTI_QUESTION_RAG_CHUNKS
                if len([str(item) for item in (runtime_atoms.get("questions") or [])]) >= 2
                else _SINGLE_QUESTION_RAG_CHUNKS
            )

            text = await self._try_llm(
                message=message,
                state=state,
                intent=intent,
                extraction=extraction,
                rag_chunks=rag_chunks[:_rag_budget],
                route_name=route.name,
                runtime_atoms=runtime_atoms,
                response_hint=response_hint,
                context_pack=context_pack,
                response_plan=response_plan,
                recent_turns=recent_turns,
                engine_facts=_engine_facts,
                persona_decision=persona_decision,
                attempt="full",
            )
            if text is not None:
                return ResponseDraft(
                    text=text,
                    source=self.provider_name,
                    approved_urls=_engine_approved_urls,
                )

            # Re-initiate the request instead of immediately conceding to a canned
            # template. Each retry drops RAG (verbatim-RAG bleed is a common cause of
            # validation rejection) and re-issues the call; the LLM is stochastic, so a
            # fresh attempt usually succeeds where the previous one was rejected.
            for retry_index in range(max(1, self.response_retry_attempts)):
                attempt_label = "reduced" if retry_index == 0 else f"retry_{retry_index}"
                text = await self._try_llm(
                    message=message,
                    state=state,
                    intent=intent,
                    extraction=extraction,
                    rag_chunks=[],
                    route_name=route.name,
                    runtime_atoms=runtime_atoms,
                    response_hint=response_hint,
                    context_pack=context_pack,
                    response_plan=response_plan,
                    recent_turns=recent_turns,
                    engine_facts=_engine_facts,
                    persona_decision=persona_decision,
                    attempt=attempt_label,
                )
                if text is not None:
                    source = (
                        f"{self.provider_name}_reduced"
                        if retry_index == 0
                        else f"{self.provider_name}_retry"
                    )
                    return ResponseDraft(
                        text=text,
                        source=source,
                        approved_urls=_engine_approved_urls,
                    )

            # Before conceding to the template, try a SECONDARY Claude model (Sonnet).
            # The primary (Opus) failing every attempt is usually HTTP 529 Overloaded,
            # which the more-available model routinely serves — keeping the reply
            # Claude-generated instead of canned.
            if self.fallback_adapter is not None:
                fb_text = await self._try_llm(
                    message=message,
                    state=state,
                    intent=intent,
                    extraction=extraction,
                    rag_chunks=[],
                    route_name=route.name,
                    runtime_atoms=runtime_atoms,
                    response_hint=response_hint,
                    context_pack=context_pack,
                    response_plan=response_plan,
                    recent_turns=recent_turns,
                    engine_facts=_engine_facts,
                    persona_decision=persona_decision,
                    attempt="fallback_model",
                    adapter_override=self.fallback_adapter,
                )
                if fb_text is not None:
                    return ResponseDraft(
                        text=fb_text,
                        source=f"{self.provider_name}_fallback_model",
                        approved_urls=_engine_approved_urls,
                    )

            # Absolute last resort: every re-initiation AND the fallback model failed
            # (provider persistently down or rejecting). Emit the safe template so the
            # customer still gets a coherent reply rather than silence.
            return ResponseDraft(
                text=template_fallback,
                source=route.name if route.name != "direct_answer" else self.provider_name,
                approved_urls=_engine_approved_urls,
            )

    async def stream(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk] | None = None,
        pricing_quote: PricingTimelineQuote | None = None,
        timeline_estimate: PricingTimelineQuote | None = None,
        pricing_missing_question: str | None = None,
        portfolio_response: PortfolioResponse | None = None,
        document_status_message: str | None = None,
        runtime_atoms: dict[Any, Any] | None = None,
        response_hint: str | None = None,
        context_pack: ContextPack | None = None,
        response_plan: ResponsePlan | None = None,
        recent_turns: list[tuple[str, str]] | None = None,
        persona_decision: Any | None = None,
    ):
        """Stream the response text as an async generator of string chunks.

        Two paths, both of which yield ≥ 1 chunk and never raise mid-stream:

        * **Real incremental streaming** — when ``self.adapter`` exposes a
          ``stream_text`` coroutine and is not in mock mode, the LLM is asked to
          produce the reply over the Anthropic Messages streaming API and each
          ``text_delta`` is yielded as it arrives.  The full text is accumulated
          and, if the streamed result passes the same safety/quality validation
          as :meth:`generate`, it is the turn.  On **any** streaming failure
          (transport error, SSE error event, or a streamed reply that fails
          validation) the method falls back to :meth:`generate` so a streaming
          hiccup never produces a broken or unsafe turn.

        * **Fallback** — when no streaming-capable adapter is available (mock /
          no-adapter dev path, or any of the above failures), :meth:`generate`
          is awaited and its text is delivered in small word-group chunks so the
          delivery is still genuinely incremental.

        The signature is kept backward-compatible with the existing callers —
        every keyword argument :meth:`generate` accepts is accepted here too.
        """
        generate_kwargs: dict[str, Any] = {
            "message": message,
            "state": state,
            "intent": intent,
            "extraction": extraction,
            "rag_chunks": rag_chunks,
            "pricing_quote": pricing_quote,
            "timeline_estimate": timeline_estimate,
            "pricing_missing_question": pricing_missing_question,
            "portfolio_response": portfolio_response,
            "document_status_message": document_status_message,
            "runtime_atoms": runtime_atoms,
            "response_hint": response_hint,
            "context_pack": context_pack,
            "response_plan": response_plan,
            "recent_turns": recent_turns,
            "persona_decision": persona_decision,
        }

        # Real incremental streaming is only viable when the adapter exposes a
        # stream_text coroutine AND is not in mock mode (mock adapters have no
        # api_key).  The generator — not the adapter — owns this decision.
        if self._streaming_viable():
            accumulated: list[str] = []
            try:
                async for chunk in self._stream_via_adapter(
                    message=message,
                    state=state,
                    intent=intent,
                    extraction=extraction,
                    rag_chunks=rag_chunks,
                    runtime_atoms=runtime_atoms,
                    response_hint=response_hint,
                    context_pack=context_pack,
                    response_plan=response_plan,
                    recent_turns=recent_turns,
                    persona_decision=persona_decision,
                ):
                    accumulated.append(chunk)
                    yield chunk
            except Exception as exc:
                logger.warning(
                    "response_stream_failed",
                    provider=self.provider_name,
                    error=str(exc),
                    note="streaming path failed mid-flight; falling back to generate()",
                )
                accumulated = None  # signal: discard partial, fall back below
            else:
                # Validate the fully-streamed text with the same gate generate()
                # uses.  If it passes, the streamed turn is complete and we stop.
                streamed_text = "".join(accumulated)
                if _safe_generated_text(streamed_text) is not None:
                    return
                logger.info(
                    "response_stream_validation_rejected",
                    provider=self.provider_name,
                    preview=streamed_text[:120],
                )
                accumulated = None  # fall back to generate()

            # NOTE: we already yielded partial chunks above.  Falling back here
            # re-yields the full validated text; the WS consumer accumulates by
            # concatenation, so a clean restart is preferable to a broken turn.

        draft = await self.generate(**generate_kwargs)
        for chunk in _chunk_text(draft.text):
            yield chunk

    def _streaming_viable(self) -> bool:
        """Return True when the adapter can do real incremental streaming.

        Requires a callable ``stream_text`` and a truthy ``api_key`` — the mock
        adapter has neither, so the fallback (word-group chunked generate())
        path is used offline and in tests without a live API.
        """
        adapter = self.adapter
        if adapter is None:
            return False
        if not callable(getattr(adapter, "stream_text", None)):
            return False
        return bool(getattr(adapter, "api_key", None))

    async def _stream_via_adapter(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk] | None,
        runtime_atoms: dict[Any, Any] | None,
        response_hint: str | None,
        context_pack: ContextPack | None,
        response_plan: ResponsePlan | None,
        recent_turns: list[tuple[str, str]] | None,
        persona_decision: Any | None,
    ):
        """Yield text deltas from ``adapter.stream_text`` for this turn.

        Builds the same system/user prompts as :meth:`_try_llm` so the streamed
        reply is grounded identically to the non-streaming path.  Any exception
        propagates to :meth:`stream`, which falls back to :meth:`generate`.
        """
        assert self.adapter is not None
        route = self.router.route(intent)
        system = _response_system_prompt(
            active_service=context_pack.active_service if context_pack is not None else None,
            persona_decision=persona_decision,
        )
        user = _response_user_prompt(
            message=message,
            state=state,
            intent=intent,
            extraction=extraction,
            rag_chunks=(rag_chunks or [])[:5],
            route_name=route.name,
            runtime_atoms=runtime_atoms or {},
            response_hint=response_hint,
            context_pack=context_pack,
            response_plan=response_plan,
            recent_turns=recent_turns,
            engine_facts=None,
            persona_decision=persona_decision,
        )
        async for chunk in self.adapter.stream_text(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1024,
            purpose="response_stream",
            system_cache_suffix=_current_datetime_line(),
        ):
            if chunk:
                yield chunk

    async def repair(
        self,
        *,
        bad_text: str,
        quality_report: ResponseQualityReport,
        response_plan: ResponsePlan,
        context_pack: ContextPack,
        tool_governance: ToolGovernanceDecision | None = None,
        response_hint: str | None = None,
    ) -> ResponseDraft:
        if self.adapter is None:
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        LLM_CALLS.labels(provider=self.provider_name, purpose="response_repair").inc()
        try:
            generated = cast(
                GeneratedResponseText,
                await self.adapter.structured(
                    system=_response_repair_system_prompt(
                        active_service=(
                            context_pack.active_service if context_pack is not None else None
                        )
                    ),
                    user=_response_repair_user_prompt(
                        bad_text=bad_text,
                        quality_report=quality_report,
                        response_plan=response_plan,
                        context_pack=context_pack,
                        tool_governance=tool_governance,
                        response_hint=response_hint,
                    ),
                    output_model=GeneratedResponseText,
                    purpose="response_repair",
                ),
            )
        except Exception as exc:
            logger.warning(
                "response_repair_provider_failed",
                provider=self.provider_name,
                error=str(exc),
            )
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        cleaned = _safe_generated_text(generated.text)
        if cleaned is None:
            return ResponseDraft(
                text=bad_text,
                source="template_no_adapter_repair_unavailable",
            )

        return ResponseDraft(text=cleaned, source=f"{self.provider_name}_repair")

    async def _try_llm(
        self,
        *,
        message: ProcessedMessage,
        state: ThreadState,
        intent: IntentVote,
        extraction: CombinedExtraction,
        rag_chunks: list[RetrievedChunk],
        route_name: str,
        runtime_atoms: dict[str, Any],
        response_hint: str | None,
        context_pack: ContextPack | None,
        response_plan: ResponsePlan | None,
        recent_turns: list[tuple[str, str]] | None = None,
        engine_facts: str | None = None,
        persona_decision: Any | None = None,
        attempt: str = "full",
        adapter_override: LLMProvider | None = None,
    ) -> str | None:
        _adapter = adapter_override or self.adapter
        assert _adapter is not None

        LLM_CALLS.labels(provider=self.provider_name, purpose=f"response_{attempt}").inc()

        try:
            generated = cast(
                GeneratedResponseText,
                await _adapter.structured(
                    system=_response_system_prompt(
                        active_service=context_pack.active_service
                        if context_pack is not None
                        else None,
                        persona_decision=persona_decision,
                    ),
                    user=_response_user_prompt(
                        message=message,
                        state=state,
                        intent=intent,
                        extraction=extraction,
                        rag_chunks=rag_chunks,
                        route_name=route_name,
                        runtime_atoms=runtime_atoms,
                        response_hint=response_hint,
                        context_pack=context_pack,
                        response_plan=response_plan,
                        recent_turns=recent_turns,
                        engine_facts=engine_facts,
                        persona_decision=persona_decision,
                    ),
                    output_model=GeneratedResponseText,
                    purpose=f"response_{attempt}",
                    system_cache_suffix=_current_datetime_line(),
                ),
            )
        except Exception as exc:
            exc_class = exc.__class__.__name__
            # Surface timeouts explicitly so they appear differently in traces.
            if "timeout" in exc_class.lower() or "Timeout" in str(exc):
                logger.warning(
                    "response_generation_timeout",
                    provider=self.provider_name,
                    attempt=attempt,
                    error=str(exc),
                    note=(
                        "LLM took too long — raise llm_request_timeout_seconds in Settings "
                        "or investigate slow response from provider."
                    ),
                )
            else:
                logger.warning(
                    "response_generation_provider_failed",
                    provider=self.provider_name,
                    attempt=attempt,
                    error=str(exc),
                )
            return None

        cleaned = _safe_generated_text(generated.text)
        if cleaned is None:
            logger.info(
                "response_generation_validation_rejected",
                provider=self.provider_name,
                attempt=attempt,
                preview=generated.text[:120],
            )
        return cleaned

    @staticmethod
    def _mock_response(
        intent: IntentVote,
        rag_chunks: list[RetrievedChunk],
        route_name: str,
    ) -> str:
        del rag_chunks, route_name

        if intent.query_primary in {
            QueryIntentType.PRICING_QUESTION,
            QueryIntentType.TIMELINE_QUESTION,
        }:
            return (
                "I can help prepare a realistic estimate, but I don’t want to guess. "
                "Please share the service mix, genre, manuscript word or page count, "
                "manuscript stage, and deadline so the quote can be scoped properly."
            )

        if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
            return (
                "Yes — I can help match samples to your project. Which service and "
                "genre should I use so the examples are actually relevant?"
            )

        if intent.query_primary == QueryIntentType.NDA_REQUEST:
            return (
                "Absolutely — confidentiality should be clear before you share the "
                "manuscript. Would you like to start the NDA step by sharing the "
                "author name, email, phone, and preferred effective date?"
            )

        if intent.query_primary == QueryIntentType.AGREEMENT_REQUEST:
            return (
                "I can help start the agreement step once the service scope is clear. "
                "Which services should the agreement cover?"
            )

        if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
            return (
                "A consultation makes sense here because the service mix needs to be "
                "scoped carefully. What manuscript stage, genre, deadline, and services "
                "should we review first?"
            )

        return (
            "I can help with the book project. What stage is the manuscript in, and "
            "which support do you need most right now: writing, editing, design, "
            "formatting, publishing, or marketing?"
        )


def _humanized_template_response(
    *,
    intent: IntentVote,
    state: ThreadState,
    message: ProcessedMessage,
    runtime_atoms: dict[str, Any],
    rag_chunks: list[RetrievedChunk],
    route_name: str,
    response_hint: str | None = None,
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
) -> str:
    del message, route_name, rag_chunks

    # A deterministic fallback template CANNOT paraphrase, so it must never splice
    # verbatim retrieved-document text into a customer reply (that produced the
    # "Welcome to BookCraft! <raw FAQ prose> ..." leak seen in chat 6211). RAG
    # grounding belongs solely in the LLM prompt, which is instructed to paraphrase
    # and is screened by the quality gate. The fallback stays generic-but-safe.

    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services)
    cta = _cta_for_intent(
        intent, runtime_atoms, state, context_pack=context_pack, response_plan=response_plan
    )

    def _single_q(body: str, question: str) -> str:
        """Append question only if body has no ? yet — ensures max 1 question mark."""
        if not question:
            return body
        if "?" in body:
            return body  # already has a question; drop the CTA
        return f"{body} {question}" if body else question

    forbid_markers = runtime_atoms.get("forbid_markers", [])
    has_guarantee_pressure = isinstance(forbid_markers, list) and "guarantee" in {
        str(item) for item in forbid_markers
    }
    template_questions = [str(item) for item in (runtime_atoms.get("questions") or [])]

    # Greeting: welcome warmly without a scoping question.
    if response_plan is not None and response_plan.primary_goal == "greeting_welcome":
        # When the visitor already has an active service (named it, or landed on a
        # service page that was anchored), acknowledge THAT service instead of the
        # generic "manuscript / publishing" welcome (chat 6573).
        active_service = getattr(context_pack, "active_service", None) if context_pack else None
        if active_service:
            svc_label = _human_service_name(str(active_service))
            return (
                f"Welcome to BookCraft! I'd be glad to help with {svc_label}. "
                "Tell me a little about your book and what you have in mind."
            )
        welcome = (
            "Welcome to BookCraft! "
            "What are you working on — is it a manuscript you're looking to publish, "
            "or are you still in the writing stage?"
        )
        return welcome

    # Multi-question turns must never be answered by a single-topic template. This is
    # the last-resort path (every LLM attempt was rejected), so there are no grounded
    # answers to give and inventing rights or fee terms would be far worse than saying
    # so. Placed above the guarantee branch because one incidental "guaranteed" inside
    # a 13-question checklist used to hand the whole turn to the bestseller speech,
    # which answered none of it and then repeated itself (chat 5876).
    if len(template_questions) >= 2:
        return _single_q(
            f"You've asked {len(template_questions)} good questions there, and they "
            "deserve exact answers rather than a guess from me — ownership, rights, and "
            "fees are things I want to get precisely right for you. Let me put a "
            "specialist on with you to go through them one by one.",
            cta,
        )

    # Guarantee/bestseller wording ONLY when the author actually pressed for a
    # guarantee. A generic complaint or objection is NOT a guarantee question — the
    # two were previously coupled, so a confused "I just told you" got answered with
    # an unprompted speech about bestseller ranks (chat 6688).
    if has_guarantee_pressure:
        return (
            "I wouldn’t want to promise a bestseller rank or a fixed sales number, "
            "because that would not be honest. What BookCraft can do is build a "
            f"realistic plan around {service_phrase}: positioning, publishing setup, "
            "launch assets, and promotion steps that give the book a stronger chance. "
            "Would you like me to scope a practical launch plan instead of a guarantee?"
        )

    if intent.query_primary.value == "complaint_or_objection":
        # Complaints get acknowledged plainly — no scoping, no pitch, no invented
        # "concern you mentioned earlier".
        return (
            "You’re right to flag that, and I’m sorry it landed wrong. "
            f"I’m still here to help with {service_phrase} whenever you’re ready — "
            "or I can bring in a specialist to pick things up directly."
        )

    if response_hint == "repeat_message":
        _body = (
            f"I’m with you — the project still looks like {service_phrase}. "
            "Rather than repeat the same overview, the useful next step is to pin down "
            "the missing project details."
        )
        return _single_q(_body, cta)

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        _known_labels = {f.label for f in context_pack.known_facts} if context_pack else set()
        _has_stage = "manuscript_status" in _known_labels
        _has_genre = "genre" in _known_labels
        if _has_stage and _has_genre:
            _known_clause = "you’ve already shared the manuscript stage and category"
        elif _has_stage:
            _known_clause = "you’ve already shared the manuscript stage"
        elif _has_genre:
            _known_clause = "you’ve already shared the category"
        else:
            _known_clause = "we have your service interest noted"
        _body = (
            f"Perfect — for {service_phrase}, {_known_clause}. I’d start "
            "by confirming the file condition, deadline, and which services should be "
            "quoted together."
        )
        return _single_q(_body, cta)

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        _genre_val = getattr(getattr(state.project, "genre", None), "value", None)
        _project_ref = str(_genre_val) if _genre_val else "your project"
        _body = (
            "Absolutely — confidentiality should be clear before you share sensitive "
            f"manuscript material. Once that’s handled, we can safely scope {service_phrase} "
            f"around {_project_ref}."
        )
        return _single_q(_body, cta)

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        _body = (
            f"I can help you get a realistic estimate for {service_phrase}. "
            "The estimate depends on manuscript condition, word or page count, genre, and deadline."
        )
        return _single_q(_body, cta)

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        _body = (
            f"For {service_phrase}, the best samples are the ones closest to your "
            "genre and project type. I’d narrow them by service, book category, and style."
        )
        return _single_q(_body, cta)

    if intent.query_primary == QueryIntentType.MANUSCRIPT_STATUS_UPDATE:
        _body = (
            "That’s great progress. "
            f"For {service_phrase}, the next step depends on whether you need "
            "creation, restructuring, or polishing."
        )
        return _single_q(_body, cta)

    if intent.query_primary == QueryIntentType.CONSULTATION_REQUEST:
        _body = (
            f"For {service_phrase}, I’d treat this as one connected book-production plan "
            "instead of separate tasks. That keeps editing, design, formatting, publishing, "
            "and launch support from happening out of order."
        )
        return _single_q(_body, cta)

    _body = (
        f"Thanks — {service_phrase} is the main direction here. "
        "I’d confirm the manuscript stage first, then map the right services around that."
    )
    return _single_q(_body, cta)


def _ordered_human_services(intent: IntentVote, runtime_atoms: dict[str, Any]) -> list[str]:
    raw_services: list[str] = []

    runtime_services = runtime_atoms.get("services", [])
    if isinstance(runtime_services, list):
        raw_services.extend(value for value in runtime_services if isinstance(value, str))

    if intent.service_primary is not None:
        raw_services.append(intent.service_primary.value)

    raw_services.extend(service.value for service in intent.service_secondary)

    negated_raw = runtime_atoms.get("negated_services", [])
    negated = (
        {value for value in negated_raw if isinstance(value, str)}
        if isinstance(negated_raw, list)
        else set()
    )

    seen: set[str] = set()
    ordered: list[str] = []
    for service in raw_services:
        if service in seen or service in negated:
            continue
        seen.add(service)
        ordered.append(service)

    if not ordered:
        return ["your book project"]

    return [_human_service_name(service) for service in ordered]


def _human_service_name(service: str) -> str:
    names = {
        "ghostwriting": "ghostwriting",
        "editing_proofreading": "editing and proofreading",
        "cover_design_illustration": "cover design and illustration",
        "interior_formatting": "interior formatting",
        "publishing_distribution": "publishing and distribution",
        "marketing_promotion": "marketing and promotion",
        "audiobook_production": "audiobook production",
        "author_website": "author website",
        "video_trailer": "video trailer",
        "fine_art_monograph": "fine-art and premium monograph publishing",
        "catalog_transition": "catalog transition and rights recovery",
        "publishing_partnership": "full-service or hybrid publishing partnership",
        "author_brand_platform": "author brand and platform strategy",
        "translation_foreign_rights": "translation and foreign-rights localization",
        "special_collector_editions": "special and collector editions",
    }
    return names.get(service, service.replace("_", " "))


def _service_phrase(services: list[str]) -> str:
    if not services:
        return "your book project"
    if len(services) == 1:
        return services[0]
    if len(services) == 2:
        return f"{services[0]} and {services[1]}"
    return f"{', '.join(services[:-1])}, and {services[-1]}"


def _pricing_single_question_cta(
    *,
    has_length: bool,
    has_genre: bool,
    has_stage: bool,
) -> str:
    """Return the single highest-priority missing pricing slot question.

    Batch 4: ask one slot at a time so the quality gate never has to repair a
    multi-slot pricing question. Priority order matches what the deterministic
    quote engine needs first.

    Priority:
      1. word/page count (treated as one "length" slot)
      2. genre/category
      3. manuscript stage
    """
    if not has_length:
        return "What rough word count or page count should I use for the estimate?"
    if not has_genre:
        return "Which genre or book category is the manuscript?"
    if not has_stage:
        return "What stage is the manuscript at — outline, first draft, or fully written?"
    # All three known — offer assumptions or specialist.
    return (
        "I have the key scoping details. Would you like a rough estimate now, "
        "or connect with a specialist for an accurate quote?"
    )


def _cta_for_intent(
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
    state: ThreadState,
    *,
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
) -> str:
    # Phone is the primary contact: if the plan's outstanding ask is the phone number
    # (e.g. an email-only contact), always surface it — even for goals that otherwise
    # suppress the CTA, like lead_created_confirmation. "Always ask, never block."
    if response_plan is not None and response_plan.next_question == "missing_phone":
        if context_pack is not None:
            mapped = _question_for_missing_fact("missing_phone", context_pack=context_pack)
            if mapped is not None:
                return mapped
        return "missing_phone"

    # Goals that must NOT produce a scoping or discovery CTA.
    if response_plan is not None and response_plan.primary_goal in {
        "lead_created_confirmation",
        "consultation_status_scheduled",
        "minimal_acknowledge",
        "complaint_recovery",
    }:
        return ""  # No CTA for these goals — the response is complete as-is.

    # ResponsePlan.next_question overrides all other CTA logic when set.
    if response_plan is not None and response_plan.next_question is not None:
        nq = response_plan.next_question
        if context_pack is not None:
            mapped = _question_for_missing_fact(nq, context_pack=context_pack)
            if mapped is not None:
                return mapped
        return nq

    if context_pack is not None:
        for missing_fact in context_pack.allowed_next_questions:
            question = _question_for_missing_fact(
                missing_fact,
                context_pack=context_pack,
            )
            if question is not None:
                return question

    has_word_count = (
        bool(runtime_atoms.get("word_counts")) or state.project.word_count.value is not None
    )
    has_page_count = (
        bool(runtime_atoms.get("page_counts")) or state.project.page_count.value is not None
    )
    has_length = has_word_count or has_page_count
    has_genre = bool(getattr(state.project.genre, "value", None))
    has_stage = bool(getattr(state.project.manuscript_status, "value", None))

    if intent.query_primary == QueryIntentType.NDA_REQUEST:
        return (
            "Would you like me to help start the NDA step if you share the author "
            "name, email, phone, and preferred effective date?"
        )

    if intent.query_primary == QueryIntentType.PORTFOLIO_REQUEST:
        if has_genre:
            return "What cover style or visual direction should I match the samples against?"
        return "Which genre or book category should I match the samples against?"

    if intent.query_primary in {
        QueryIntentType.PRICING_QUESTION,
        QueryIntentType.TIMELINE_QUESTION,
    }:
        return _pricing_single_question_cta(
            has_length=has_length,
            has_genre=has_genre,
            has_stage=has_stage,
        )

    if intent.query_primary == QueryIntentType.READY_TO_BUY:
        return (
            "What launch date are you aiming for, and do you want the full service "
            "bundle scoped together?"
        )

    general_missing_fields: list[str] = []
    if not has_length:
        general_missing_fields.append("rough word count or page count")
    if not has_genre:
        general_missing_fields.append("genre")
    if not has_stage:
        general_missing_fields.append("manuscript stage")

    if general_missing_fields:
        if len(general_missing_fields) == 1:
            return (
                f"Can you share the {general_missing_fields[0]} "
                "so I can guide the next step properly?"
            )
        return (
            f"Can you share the {', '.join(general_missing_fields[:-1])}, and "
            f"{general_missing_fields[-1]} so I can guide the next step properly?"
        )

    return (
        "Since the basics are clear, would you like to move toward a cover-design "
        "scope, a quote, or a consultation?"
    )


def _question_for_missing_fact(
    missing_fact: str,
    *,
    context_pack: ContextPack,
) -> str | None:
    # Indefinite call time → offer concrete half-hour openings to pick from.
    if missing_fact == "preferred_call_time_slots":
        slots = list(getattr(context_pack, "suggested_call_slots", None) or [])
        if slots:
            if len(slots) == 1:
                options = slots[0]
            elif len(slots) == 2:
                options = f"{slots[0]} or {slots[1]}"
            else:
                options = f"{', '.join(slots[:-1])}, or {slots[-1]}"
            return (
                "To lock in your call, which of these works best — "
                f"{options}? If none fit, just tell me a day and time "
                "(Monday–Friday, 10 AM to 7 PM Central) and I'll set it up."
            )
        # No precomputed slots — fall back to the open call-time ask.
        return (
            "What specific day and time works best for a call? "
            "Our specialists are available Monday–Friday, 10 AM to 7 PM Central Time."
        )

    questions = {
        "cover_style": "What cover style or visual direction should I use for the design scope?",
        "word_or_page_count": "What rough word count or page count should I use?",
        "deadline": "What deadline or launch window should I use?",
        "genre": "What genre or book category should I use?",
        "manuscript_stage": "What manuscript stage should I use?",
        # Greeting and consultation interest next-questions.
        "how_can_we_help": (
            "What are you looking to achieve with your book — "
            "writing, editing, design, publishing, or marketing?"
        ),
        "consultation_interest": (
            "Would you like to connect with a BookCraft specialist for a free consultation?"
        ),
        "manuscript_upload_pitch": (
            "Since you have written material, would you like to share it here? "
            "Our specialists offer a free editorial assessment — "
            "just upload your manuscript using the 📎 attach button below. "
            "Our team will review it and provide professional feedback before your consultation."
        ),
        "preferred_call_time": (
            "What day and time works best for a call? "
            "Our specialists are available Monday–Friday, 10 AM to 7 PM Central Time."
        ),
        "preferred_call_timezone": "What timezone are you in so I can confirm the time slot?",
        "name_and_email_or_phone": (
            "Could I get your name and a phone number? "
            "An email address is welcome too if you have one handy."
        ),
        "missing_phone": (
            "I also need a phone number to complete your booking — "
            "that's how our specialist will reach you."
        ),
        "missing_email": (
            "Thanks — do you also have an email address I can add? "
            "Totally optional, but useful for sending confirmations."
        ),
        "clarify_intent": "Could you tell me a bit more about what you're looking for?",
    }
    question = questions.get(missing_fact)
    if question is None:
        return None

    lowered = question.casefold()
    if any(marker.casefold() in lowered for marker in context_pack.disallowed_next_questions):
        return None
    return question


def _contains_doc_artifacts(text: str) -> bool:
    patterns = [
        r"\bquote engine\b",
        r"\bpricing engine\b",
        r"\bapproved engine\b",
        r"\bdeterministic\b",
        r"\bNDA queue\b",
        r"\bdocument queue\b",
        r"\bapproved template\b",
        r"\bapproved tool\b",
        r"\btool output\b",
        r"\bRAG\b",
        r"\bruntime atoms\b",
        r"\bprovider votes\b",
        r"\bclassifier\b",
        r"\bbackend\b",
        r"^\s*#{1,6}\s",
        r"\n\s*\|.*\|",
        r"\bSource:\s*",
        r"##\s*Related Services",
        r"##\s*Service Tiers",
        r"###\s*Cover layouts",
        r"approved registry samples only",
        r"This is a .*stage conversation",
        r"Pricing tiers and rates are maintained",
    ]
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def _has_human_opener(text: str) -> bool:
    head = text.lstrip()[:40]
    if not head:
        return False
    if not head[0].isupper():
        return False
    return not head.startswith(("|", "-", "#", "*", "```", ">"))


def _clean_customer_text(text: str) -> str:
    cleaned = re.sub(r"\bSource:\s*[^\n]+", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"^\s*#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n\s*\|.*\|", "", cleaned)
    return cleaned.strip()


_RETAIL_ORDER_RE = re.compile(
    r"\b(?:"
    r"\d+\s+copies\s+of\b|"
    r"bulk\s+(?:order|purchase|buy|copies)|"
    r"(?:order|purchase|buy)\s+\d+\s+(?:copies|units|books)|"
    r"isbn\s+\d{3}[\s-]?\d|"
    r"tiered\s+discount|"
    r"free\s+freight|"
    r"mixed[\s-]title\s+bulk|"
    r"wholesale\s+(?:books?|copies|price)|"
    r"(?:retail|resale)\s+(?:price|order|copies)|"
    r"units?\s+total\b|"
    r"delivery\s+(?:time|cost)\s+to\s+zip"
    r")\b",
    re.IGNORECASE,
)


def _is_retail_book_order(text: str) -> bool:
    """Return True when the message is about buying retail/wholesale copies of books.

    BookCraft helps authors PUBLISH their own work; it does not sell retail copies
    of other publishers' titles to buyers. Detecting this early prevents Claude
    from inventing price/discount/freight figures that fail the quality gate.
    """
    return bool(_RETAIL_ORDER_RE.search(text))


def _chunk_text(text: str, *, words_per_chunk: int = 5) -> list[str]:
    """Split text into a few word-group chunks for incremental delivery.

    Used only on the streaming fallback path (no live API): yields the full
    text in ~``words_per_chunk``-word groups so a consumer receives multiple
    chunks whose concatenation reproduces the original text exactly (whitespace
    is preserved by splitting with a capturing regex, not ``str.split``).  A
    single-word or empty string yields one chunk so the contract "≥ 1 chunk" and
    "concatenation == text" both hold.  ``generate()`` itself is untouched.
    """
    if not text:
        return [text]
    # Split into [word, sep, word, sep, ...] keeping separators so we can
    # rejoin losslessly.  Group whole words (skipping the separator tokens)
    # into runs of words_per_chunk and emit each run with its trailing
    # whitespace attached.
    tokens = re.split(r"(\s+)", text)
    chunks: list[str] = []
    current: list[str] = []
    words_in_current = 0
    for token in tokens:
        current.append(token)
        if token and not token.isspace():
            words_in_current += 1
            if words_in_current >= words_per_chunk:
                chunks.append("".join(current))
                current = []
                words_in_current = 0
    if current:
        chunks.append("".join(current))
    return chunks or [text]


def _truncate_on_word_boundary(text: str, max_chars: int) -> str:
    """Truncate text to max_chars on a word boundary, appending '…' if cut."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rsplit(" ", 1)[0]
    return truncated + "…"


def _recent_turns_prompt_section(
    recent_turns: list[tuple[str, str]] | None,
) -> str:
    """Render the last ≤3 conversation turns as a plain block for the LLM.

    Each turn is (user_text, assistant_text), oldest first.  Truncates each
    side to 300 chars on a word boundary so the prompt stays compact.
    """
    if not recent_turns:
        return ""
    capped = recent_turns[-3:]
    lines = ["\nRecent conversation (oldest first; use to stay coherent):"]
    for user_text, asst_text in capped:
        u = _truncate_on_word_boundary(user_text.strip(), 300)
        a = _truncate_on_word_boundary(asst_text.strip(), 300)
        lines.append(f"  Author: {u}")
        lines.append(f"  You:    {a}")
    lines.append(
        "Continue naturally. Do NOT repeat a question you already asked, "
        "and never ignore an answer the author just gave."
    )
    return "\n".join(lines)


_BUSINESS_TZ_NAME = "America/Chicago"


def _current_datetime_line(now: datetime | None = None) -> str:
    """A human-readable 'now' the model uses to ground all date/time reasoning."""
    if now is None:
        now = datetime.now(ZoneInfo(_BUSINESS_TZ_NAME))
    # e.g. "Monday, June 22, 2026, 3:14 PM Central Time (CDT)"
    pretty = now.strftime("%A, %B %d, %Y, %-I:%M %p")
    tz_abbr = now.strftime("%Z")
    return (
        "Current date and time (authoritative — use this as 'now' for ALL "
        f"scheduling and date reasoning): {pretty} Central Time ({tz_abbr}). "
        f"Today is {now.strftime('%Y-%m-%d')}.\n"
        "- Resolve every relative day the customer mentions ('Monday', 'tomorrow', "
        "'the 22nd', 'next week') against this current date.\n"
        "- NEVER confirm, repeat, or propose a date or time that is in the past "
        "relative to now. If the customer names a day/time that has already passed, "
        "say so plainly and ask for an upcoming day and time instead.\n"
        "- Do NOT invent or guess a specific calendar date. Only state a date once "
        "it has been confirmed by the scheduling engine; otherwise reflect back the "
        "customer's own words for the day/time.\n\n"
    )


def _response_system_prompt(
    active_service: str | None = None,
    persona_decision: Any | None = None,
) -> str:
    # NOTE: the current date/time is intentionally NOT included here. It is
    # delivered as a separate uncached system block (see `_current_datetime_line`
    # passed as `system_cache_suffix` in `_try_llm` / `_stream_via_adapter`) so
    # this stable prompt can be prompt-cached across turns and threads.
    style = _STYLE_POLICY.style_instructions(active_service=active_service)

    # Persona: build identity instruction based on assigned representative name.
    rep_name = (
        getattr(persona_decision, "representative_name", None) if persona_decision else None
    )
    is_identity_q = (
        getattr(persona_decision, "is_identity_question", False) if persona_decision else False
    )
    is_first = (
        getattr(persona_decision, "is_first_introduction", False) if persona_decision else False
    )

    if rep_name:
        identity_instruction = (
            f"Your name is {rep_name}, a BookCraft publishing consultant.\n"
        )
        if is_identity_q and is_first:
            identity_instruction += (
                f"The author just asked if you are a bot or AI. "
                f"Introduce yourself naturally as {rep_name} from BookCraft and continue helping. "
                f"Do NOT say 'I am an AI', 'I am a bot', 'I am a human', 'very much human', "
                f"or any explicit claim about your nature. Just be {rep_name}.\n"
            )
        elif is_identity_q:
            identity_instruction += (
                f"The author asked about your identity again. "
                f"Simply confirm you are {rep_name} from BookCraft and move forward.\n"
            )
    else:
        identity_instruction = (
            "You are a BookCraft publishing consultant helping authors. "
            "Never describe yourself as an AI, bot, automated system, "
            "'very much human', or make any explicit claim about your nature.\n"
        )

    return (
        f"{identity_instruction}\n"
        "Your job is to help them get clarity and move one concrete step closer "
        "to a quote, sample request, NDA, or consultation.\n\n"
        f"{style}\n\n"
        "Source of truth: the BookCraft context provided in this prompt is your source "
        "of facts. State BookCraft facts from it in your own words. When something is "
        "not covered in the provided context, say you can confirm that detail for them "
        "rather than inventing it.\n\n"
        "Sharing their story:\n"
        "When the author is telling you their story or the story behind their book — "
        "personal history, life events, or emotional context — stay with them. React to "
        "the specific human detail they just shared, show genuine interest, and let them "
        "keep going. Do NOT redirect to pricing, scoping, or scheduling while they are "
        "opening up. There is a natural moment for next steps once the story has room to "
        "breathe; forcing a consultation or contact ask mid-story reads as not listening.\n\n"
        "Gap 7 — Topic switches and contradictions:\n"
        "- When the author pivots to a different service (e.g. 'forget editing, I want a cover'), "
        "acknowledge the switch explicitly, drop scoping from the prior service, and move "
        "cleanly into the new topic.\n"
        "- When a fact the author stated earlier contradicts something they just said, "
        "surface it gently once ('Earlier you mentioned X — should I use Y instead?') "
        "rather than silently picking one value.\n\n"
        "Gap 8 — Call opt-out and postponement (respect both immediately):\n"
        "- When the author says they would rather be TEXTED than called ('can they text?', "
        "'I'm bad at calling', 'can we do this over text'), that is settled the moment they say "
        "it. Confirm the specialist will text them, and NEVER ask what time works for a call, "
        "what day suits them, or their timezone — there is no call to schedule. Their phone "
        "number is still valid; it just will not ring.\n"
        "- When the author postpones ('we might do it next month', 'not right now', "
        "'I'm not doing it until next month'), acknowledge it warmly, make clear their details "
        "are saved and nothing expires on them, and STOP pushing the booking. Do not ask for a "
        "day, a time, or a timezone. Do not re-raise scheduling on later turns unless the author "
        "brings it up again. Answer whatever they actually ask.\n"
        "- Never claim a call is 'locked in', 'all set', 'confirmed', or that a specialist will "
        "call at a specific time, unless the system has actually created that booking. If no "
        "booking exists, say what genuinely happens next instead.\n\n"
        "BookCraft contact information (use ONLY these exact details when asked):\n"
        "  Email   : inquiry@bookcraftpublishers.com\n"
        "  Phone   : 888 905 0868\n"
        "  Address : 12828 Willow Centre Dr Ste D #225, Houston TX, USA 77066\n"
        "Never invent or alter these details. If the author asks how to reach BookCraft "
        "or for contact information, provide exactly the above.\n"
        "- When the author asks for an email or a way to reach you / respond later / follow up "
        "(e.g. 'is there an email I can respond to?', 'can I email you back?', 'how do I contact "
        "you?'), ALWAYS give them inquiry@bookcraftpublishers.com. NEVER deflect with 'no need to "
        "email' or by insisting they book or share details in chat first — give the email, then "
        "they can proceed however they prefer.\n"
        "Manuscript / document submission: When an author says they want to send their manuscript, "
        "document, or file by email (e.g. 'I can't upload', 'I'll email it', 'my document won't load'), "
        "tell them they can send it to inquiry@bookcraftpublishers.com and that the team will follow up.\n"
        "File or attachment received: When the user uploads a file or attachment:\n"
        "- ALWAYS confirm receipt by the file's name when the known facts include "
        "'attachment_filename' — name the file in your acknowledgement (e.g. \"Got your file "
        "'Chapter 2.docx'\"). If no filename is provided, acknowledge by type instead.\n"
        "- Quick human look (only using facts you are given — you did NOT open the file): if "
        "'attachment_page_count' or 'attachment_word_count' is present, mention the size "
        "naturally and approximately (e.g. \"a ~134-page draft\", \"around 40,000 words\"). If "
        "'attachment_image_dimensions' is present for a cover/design, you may note the format "
        "and size (e.g. \"a 1600x2400 cover\"). If 'attachment_excerpt' is present, give ONE "
        "brief, warm first impression of the subject or tone IN YOUR OWN WORDS (e.g. \"reads "
        "like the opening of a family memoir\", \"looks like a business how-to\"). This is a "
        "glance, like a person flipping to the first page — NOT a review.\n"
        "- HARD LIMITS on the quick look: never quote or paraphrase sentences from the "
        "excerpt; never claim to have read, finished, studied, or assessed the manuscript; "
        "never invent plot, characters, quality judgements, or details not present in the "
        "facts; keep the impression to a single short clause and hand off to the specialist "
        "for the real assessment. If no excerpt/counts are given, simply acknowledge by name "
        "and route to the specialist — do NOT guess at contents.\n"
        "- If the attachment is a manuscript or writing sample, acknowledge warmly and confirm "
        "the free editorial assessment: \"Got your file 'Chapter 2.docx' — our editorial "
        "specialist will review it and provide a professional assessment. To send you the report, "
        "I just need your name, phone number, and preferred timezone for your consultation call.'\n"
        "- If the attachment is something else (cover design, brief, etc.), acknowledge by name and "
        "route to specialist assessment: \"Got your file 'brief.pdf' — a specialist will review it. "
        "To connect you, I just need your name, phone number, and timezone.'\n"
        "Free editorial assessment pitch: When the context hint includes "
        "'MANUSCRIPT UPLOAD PITCH', weave a natural one-time offer into your response "
        "after acknowledging the author's work. Example: "
        "'Since you have [5 chapters / a partial draft / your notes] written, "
        "our specialists can do a free editorial assessment — just upload your manuscript "
        "using the 📎 attach button at the bottom of this chat. "
        "Our editorial team will give you professional feedback before your consultation.' "
        "Say this once, blend it naturally, never repeat it.\n"
        "CRITICAL: Never use the above contact details as a substitute for scheduling a consultation. "
        "When an author asks to schedule a consultation, collect their details in this chat — "
        "do NOT tell them to email or call us instead.\n\n"
        "Consultation scheduling flow:\n"
        "CONSULTATION MODE — applies once consultation has been requested:\n"
        "When the customer has asked for a consultation (in ANY form — 'I want a consultation', "
        "'schedule me a specialist', 'I want to speak to someone', 'yes', 'sure', 'go ahead', "
        "'just schedule me', or any frustration like 'stop talking and book it') — "
        "you are in CONSULTATION MODE. In this mode:\n"
        "- Do NOT ask about genre, word count, service type, manuscript stage, or ANY scoping detail.\n"
        "- Do NOT re-ask for name, phone, or email if already captured — check Known Facts.\n"
        "- If the customer raises a service topic mid-consultation, acknowledge in ONE sentence "
        "('Our specialist will cover that on the call') and return to contact collection.\n"
        "- Collect only what is still missing: name → phone → timezone → email → preferred time.\n"
        "- As soon as all required contact info is captured, confirm the booking and stop.\n"
        "When the author agrees to a consultation — including affirmative replies like "
        "'yes', 'sure', 'yes that would help', 'please set that up', 'that sounds good', "
        "'go ahead', 'absolutely' — immediately move to collecting their contact details. "
        "Do NOT ask about manuscript stage, word count, genre, or any other scoping question "
        "after they say yes to a consultation. That information can wait.\n"
        "When the author requests a consultation (in any phrasing), collect these in order:\n"
        "(1) Name — if not yet captured.\n"
        "(2) Phone number — preferred; ask for it first. If the customer says they cannot "
        "provide a phone (hacked, privacy, no phone), accept their email as the contact "
        "method and proceed — do NOT keep asking for phone.\n"
        "(3) Email — always ask for it. If no phone, email is required; if phone was given, "
        "email is optional but welcome.\n"
        "(4) Timezone — REQUIRED so the call is scheduled at the right local time.\n"
        "(5) Preferred date and time — REQUIRED before confirming. When asking, always include: "
        "'Our specialists are available Monday–Friday, 10 AM to 7 PM Central Time.' "
        "Do NOT say 'you're all set', 'a specialist will be in touch', 'confirmed', or any "
        "closing phrase until the preferred date AND time is captured. "
        "The date and time MUST be in the future relative to the current date above — if the "
        "customer names a day or time that has already passed, point it out and ask for an "
        "upcoming slot before booking.\n"
        "Ask for one missing piece at a time. "
        "Never redirect them to contact BookCraft manually.\n\n"
        "After a lead is created:\n"
        "Once a lead is confirmed, immediately suggest scheduling a free consultation. "
        "Frame it as the natural next step: 'Would you like to lock in a quick call with one "
        "of our specialists? I just need your phone number and timezone to set that up.'\n\n"
        "Language disambiguation:\n"
        "- 'print a sample' or 'sample copy' or 'proof copy' in the context of publishing "
        "means the author wants a physical proof of THEIR OWN book — this is an Interior "
        "Formatting or Publishing service question, NOT a request for BookCraft portfolio samples.\n"
        "- 'show me samples' or 'see your work' or 'portfolio examples' means they want to "
        "view BookCraft's existing work — that IS a portfolio request.\n"
        "- 'publish a journal' is a legitimate BookCraft Publishing & Distribution service. "
        "Welcome them and ask about their journal (page count, format, platform target).\n\n"
        "BookCraft scope — what we DO and DO NOT do:\n"
        "BookCraft helps AUTHORS publish their own original books. Our services are:\n"
        "Ghostwriting, Editing & Proofreading, Cover Design, Interior Formatting, "
        "Publishing & Distribution, Marketing, Audiobook Production, Video Trailer, "
        "Author Website.\n\n"
        "We do NOT offer any service or product not related to books or books publishing.\n"
        "When someone asks about buying copies of published books (bulk orders, ISBNs, "
        "tiered discounts, freight to a zip code, etc.), gently clarify that BookCraft "
        "serves authors who want to PUBLISH their own work, not buyers purchasing "
        "existing titles — and suggest they contact the publisher or a book distributor.\n\n"
        "What you must NOT do:\n"
        "- Never quote a price, cost, or fee. If pricing comes up, redirect warmly: "
        "'For an accurate quote, a free consultation with one of our specialists is the "
        "best next step — I can set that up for you.'\n"
        "- Never promise a specific delivery timeline or turnaround time. "
        "If the author asks how long something takes, invite a consultation rather than "
        "committing to dates or week ranges.\n"
        "- If you attempt an action (scheduling, NDA, lead creation) that is not yet "
        "confirmed or was blocked, tell the user honestly: 'I wasn't able to complete "
        "that from here — our team will follow up to sort it out.' "
        "Never claim an action succeeded if it did not.\n"
        "- Never output these internal terms: backend, classifier, runtime atoms, "
        "provider votes, RAG, tool_governance, action_plan, deterministic engine, "
        "quote engine, approved engine, tool output, Source:, Context:, Action plan:. "
        "Using any of these will break the response.\n"
        "- Only address the author by a name listed under Known Facts as their OWN "
        "name. NEVER address them by a name that appears inside a book title or after "
        "'written by' / 'by' / 'authored by' — that is the book's author (often the same "
        "person, but not always), not a confirmed form of address. If no author name is "
        "known, don't use a name at all.\n"
        "- Do not ask again for facts already listed under "
        "'What we already know about the project'.\n"
        "- Do not redirect an author to contact BookCraft by email or phone when they "
        "ask to schedule a consultation. Collect their details here and schedule it directly.\n"
        "- Do not restate facts the author already gave you (page count, genre, format, "
        "service). Reference them only when genuinely relevant to the current message, "
        "not as a default opener.\n"
        "- Do not use markdown headings, tables, bullet lists, or Source labels.\n"
	"- Do not redirect an author to contact BookCraft by email or phone when they ask to "
        "schedule a consultation. Collect their details here and schedule it directly.\n"
        "- Do not write paragraphs. This is a chat window. Each response is 1–2 short sentences, "
        "40 words maximum. If more detail is truly needed, break it into a follow-up turn "
        "rather than one long block.\n"
        "- Never end a response mid-thought or mid-sentence. Every response must be a complete, "
        "self-contained statement or question.\n"
        "- Write the way a knowledgeable colleague speaks in chat: direct, unhesitating, "
        "no filler phrases, no throat-clearing.\n\n"
        "RAG context and grounded engine facts, if provided, are authoritative. "
        "NEVER copy RAG text verbatim into your reply — always paraphrase in your own "
        "conversational words. If you quote a RAG section header, title, or bullet list "
        "directly, that is a quality failure. Synthesize the information, don't paste it.\n\n"
        'Output protocol: respond with one JSON object: {"text": "your reply"} '
        "and nothing else. The text field is plain prose, no markdown."
    )


def _response_repair_system_prompt(active_service: str | None = None) -> str:
    style = _STYLE_POLICY.style_instructions(active_service=active_service)
    return (
        "You are repairing a customer-facing BookCraft assistant response. "
        "Use only the facts and guidance provided below, and write a clean reply "
        "that the customer would actually receive.\n\n"
        f"{style}\n\n"
        "Do not use backend, classifier, runtime atoms, provider votes, RAG, tool_governance, "
        "action_plan, deterministic engine, quote engine, Source:, Context:, or Action plan:.\n"
        "Do not quote the original system or prompt. "
        "Do not mention internal labels or trace data.\n"
        'Output protocol: respond with one JSON object: {"text": "your reply"} and nothing else.'
    )


def _response_user_prompt(
    *,
    message: ProcessedMessage,
    state: ThreadState,
    intent: IntentVote,
    extraction: CombinedExtraction,
    rag_chunks: list[RetrievedChunk],
    route_name: str,
    runtime_atoms: dict[str, Any],
    response_hint: str | None = None,
    context_pack: ContextPack | None = None,
    response_plan: ResponsePlan | None = None,
    recent_turns: list[tuple[str, str]] | None = None,
    engine_facts: str | None = None,
    persona_decision: Any | None = None,
) -> str:
    del extraction, route_name

    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services) if services else "their book project"

    known: list[str] = []
    if getattr(state.project.genre, "value", None):
        known.append(f"genre: {state.project.genre.value}")
    if state.project.word_count.value is not None:
        known.append(f"word count: {state.project.word_count.value}")
    if state.project.page_count.value is not None:
        known.append(f"page count: {state.project.page_count.value}")
    if getattr(state.project.manuscript_status, "value", None):
        known.append(f"manuscript status: {state.project.manuscript_status.value}")
    # Always surface captured contact info so the LLM never re-asks for it — but keep
    # form-submitted details in a SEPARATE bucket. Listing a name pulled from a signup
    # form next to facts the author actually said leads the model to claim they said it
    # here, which is exactly how chat 5876 produced "you shared it earlier in our chat".
    external_contact: list[str] = []
    for _label, _field in (
        ("name", state.personal.name),
        ("email", state.personal.email),
        ("phone", state.personal.phone),
    ):
        _value = getattr(_field, "value", None)
        if not _value:
            continue
        if getattr(_field, "source", None) == Source.EXTERNAL_FORM:
            external_contact.append(f"author {_label}: {_value}")
        else:
            known.append(f"author {_label}: {_value}")
    known_str = "; ".join(known) if known else "nothing confirmed yet"
    external_contact_str = _external_contact_clause(external_contact)
    multi_question_str = _multi_question_clause(
        [str(item) for item in (runtime_atoms.get("questions") or [])]
    )

    # C1: authoritative confirmed-consultation facts. Once an appointment exists,
    # the model may restate ONLY these exact values and must never invent or drift
    # the date, time, specialist, or timezone (audit chat 6070).
    confirmed_consultation_str = _confirmed_consultation_clause(state)

    _cp_forbidden: set[str] = set(context_pack.forbidden_reasks) if context_pack else set()
    missing: list[str] = []
    if state.project.word_count.value is None and state.project.page_count.value is None:
        missing.append("word or page count")
    if not getattr(state.project.genre, "value", None):
        missing.append("genre")
    # Only list manuscript stage as missing if it is genuinely missing AND not suppressed.
    if not getattr(state.project.manuscript_status, "value", None) and not (
        _cp_forbidden & {"manuscript_stage", "manuscript_status", "manuscript stage"}
    ):
        missing.append("manuscript stage")
    missing_str = ", ".join(missing) if missing else "no major basics missing"

    intent_label = intent.query_primary.value.replace("_", " ")

    # Phone number context: if the bot just asked for contact and the author gave
    # a bare number with 10+ digits, treat it as their phone number — not a word count.
    _contact_atoms = runtime_atoms.get("phones") or []
    _bare_number_note = ""
    if (
        response_plan is not None
        and response_plan.next_question in {
            "name_and_email_or_phone",
            "missing_phone",
            "missing_email",
            "preferred_call_time",
        }
        and not _contact_atoms
    ):
        # Bare 10+ digit number in this message is almost certainly a phone number.
        import re as _re
        _bare = _re.findall(r"\b\d{10,}\b", message.raw or "")
        if _bare:
            _bare_number_note = (
                f"\nNote: the author provided '{_bare[0]}' which is a phone number "
                "(10+ digit bare number). Treat it as their contact phone, not a word count."
            )

    # Gap 3 (mission audit): surface secondary intents so bundled questions are not dropped.
    secondary_intents = getattr(intent, "query_secondary", None) or []
    secondary_str = ""
    if secondary_intents:
        secondary_labels = [q.value.replace("_", " ") for q in secondary_intents[:3]]
        secondary_str = (
            f"\nThe author also asked about: {', '.join(secondary_labels)}. "
            "Address or acknowledge both — do not let the second question vanish."
        )

    negated = runtime_atoms.get("negated_services") or []
    negated_str = (
        f"\nThey explicitly do NOT want: {', '.join(str(item) for item in negated)}."
        if isinstance(negated, list) and negated
        else ""
    )

    rag_notes = ""
    if rag_chunks:
        notes: list[str] = []
        # Gap 5 (mission audit): raised from 3×400 to 5×600 — wider grounding window.
        for chunk in rag_chunks[:5]:
            snippet = (chunk.content or "")[:600].strip().replace("\n", " ")
            if snippet:
                notes.append(f"- {snippet}")
        if notes:
            rag_notes = (
                "\n\nBookCraft grounding context "
                "(authoritative — state facts from this in your own words; "
                "offer to confirm when not covered here):\n" + "\n".join(notes)
            )

    hint_str = (
        "\nContext control note for this turn: "
        f"{response_hint} "
        "You must not ask again for known facts listed here."
        if response_hint
        else ""
    )
    context_pack_str = _context_pack_prompt_section(context_pack)
    response_plan_str = _response_plan_prompt_section(response_plan)

    # Step 2 (tone fix): inject recent conversation history so the LLM can
    # relate its reply to what it already said.  Cap at last 3 turns,
    # truncate each side at 300 chars on a word boundary.
    history_str = _recent_turns_prompt_section(recent_turns)

    # Persona note: remind LLM of identity when relevant.
    _persona_note = ""
    if persona_decision is not None:
        _rep = getattr(persona_decision, "representative_name", None)
        _is_identity = getattr(persona_decision, "is_identity_question", False)
        if _rep and _is_identity:
            _persona_note = (
                f"\nIdentity note: The author asked if you are a bot/AI. "
                f"Simply be {_rep} from BookCraft — do NOT say 'I am an AI', "
                f"'I am a bot', 'I am human', or 'very much human'. "
                f"Just introduce yourself and keep helping."
            )
        elif _rep:
            _persona_note = f"\nYour name this conversation: {_rep} (BookCraft consultant)."

    # Step 4 (tone fix): grounded engine output facts (approved pricing, scope detection, etc.).
    if engine_facts:
        _facts_label = (
            "Scope detection context"
            if engine_facts.startswith("OUT-OF-SCOPE")
            else "Grounded engine facts (approved — use these directly; do NOT invent numbers)"
        )
        engine_facts_str = f"\n{_facts_label}:\n{engine_facts}"
    else:
        engine_facts_str = ""

    return (
        f'The author just wrote:\n"{message.normalized}"\n\n'
        "What I can tell from this message:\n"
        f"- They seem to be asking about: {intent_label}\n"
        f"- Services in scope: {service_phrase}\n"
        # Explicitly scoped to the conversation: these facts accumulate across turns
        # and are NOT all inferable from the message quoted above.
        "- What we already know about the project, gathered over this conversation "
        f"so far: {known_str}\n"
        f"- What we still need: {missing_str}"
        f"{multi_question_str}"
        f"{external_contact_str}"
        f"{confirmed_consultation_str}"
        f"{secondary_str}"
        f"{_persona_note}"
        f"{negated_str}"
        f"{hint_str}"
        f"{context_pack_str}"
        f"{response_plan_str}"
        f"{_bare_number_note}"
        f"{engine_facts_str}"
        f"{history_str}"
        f"{rag_notes}\n\n"
        "Write the next reply now."
    )


def _multi_question_clause(questions: list[str]) -> str:
    """Prompt clause for a turn that asks several questions at once.

    Without this the model answers whichever question it latches onto and drops the
    rest — an author's 13-point rights checklist came back with a single sentence
    about launch plans (chat 5876).

    The "no question marks" rule is load-bearing, not style: both the style policy and
    the quality gate count raw '?' characters and reject any draft with more than one,
    so a reply that politely restates each question before answering it gets thrown
    away and the turn collapses to a canned fallback. Answers must therefore be stated
    directly, with the single '?' reserved for the closing follow-up.
    """
    if len(questions) < 2:
        return ""
    listed = "\n".join(f"  {index}. {question}" for index, question in enumerate(questions, 1))
    return (
        f"\n\nThe author asked {len(questions)} separate questions in this one message:\n"
        f"{listed}\n"
        "How to reply to this turn:\n"
        "- Answer EVERY question above, in the order asked. Answering only some of them "
        "is a failure, however awkward the remainder are.\n"
        "- Do NOT restate or quote the questions back, and do NOT put a question mark on "
        "any answer. State each answer directly as a fact.\n"
        "- One short clause or sentence per answer. Run them together as flowing prose. "
        "The usual 1-2 sentence / 40-word limit does NOT apply to this reply — length "
        "here comes from covering every question, not from padding.\n"
        "- If you do not know one, say plainly that you will have the specialist confirm "
        "that specific point. Never invent rights, fees, royalties, or contract terms.\n"
        "- You may add at most ONE short follow-up question of your own, at the very end, "
        "after every question above is answered. That is the only '?' allowed."
    )


def _external_contact_clause(external_contact: list[str]) -> str:
    """Prompt clause for contact details the author submitted OUTSIDE this chat.

    These are real, customer-supplied values (a signup or consultation form), so the
    bot may use them and must not re-ask. But the author never typed them here, and
    the model has no other way to know that — so state the origin explicitly and give
    it the honest answer to "where did you get my name?" (chat 5876).
    """
    if not external_contact:
        return ""
    details = "; ".join(external_contact)
    return (
        "\n\nContact details we already hold from OUTSIDE this conversation "
        f"({details}):\n"
        "- Origin: a signup or consultation form the author submitted to BookCraft "
        "previously — possibly months ago. They did NOT say this in this chat.\n"
        "- You may use these (address them by name, don't re-ask for them).\n"
        "- NEVER say or imply the author gave you these details in this chat, and "
        "never invent any other origin for them.\n"
        "- If the author asks where you got their details, tell them the truth "
        "plainly and directly: it came from the signup form they filled in with "
        "BookCraft earlier, not from this conversation. Answer that question FIRST, "
        "before anything else, and offer to correct or remove it."
    )


def _confirmed_consultation_clause(state: ThreadState) -> str:
    """Authoritative confirmed-appointment facts for the prompt (audit C1).

    Returns an empty string until a consultation is actually booked. Once booked,
    it pins the specialist, date/time, and timezone so the model restates only
    these exact values and never fabricates or drifts them on later turns.
    """
    consult = getattr(getattr(state, "sales_actions", None), "consultation", None)
    if consult is None:
        return ""
    if not (consult.confirmed_appointment_id and consult.confirmed_display_time):
        return ""

    csr = consult.csr_name or "your specialist"
    when = consult.confirmed_display_time
    tz_note = ""
    if consult.confirmed_customer_display_time and (
        consult.confirmed_customer_display_time != when
    ):
        tz_note = f" (the customer's local time: {consult.confirmed_customer_display_time})"

    return (
        "\n- CONFIRMED CONSULTATION — AUTHORITATIVE, do not alter: booked with "
        f"{csr} for {when}{tz_note}. "
        "When the appointment comes up, restate EXACTLY this specialist and this "
        "date/time — never invent, guess, or change the day, time, specialist, or "
        "timezone. If the customer asks to change it, say you'll get it updated "
        "rather than stating a different time yourself."
    )


def _build_repair_context(
    *,
    response_plan: ResponsePlan,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None = None,
) -> dict[str, object]:
    repair_context: dict[str, object] = {
        "repair_goal": (
            "Rewrite the response to remove quality failures and keep the "
            "customer-facing guidance clear."
        ),
        "must_keep": response_plan.acknowledge_facts or [],
        "must_not_ask": context_pack.forbidden_reasks or [],
    }
    if response_plan.next_question is not None:
        repair_context["next_question"] = response_plan.next_question
    if tool_governance is not None and tool_governance.blocked_message:
        repair_context["blocked_message"] = tool_governance.blocked_message
    if context_pack.active_service is not None:
        repair_context["active_service"] = context_pack.active_service
    return repair_context


def _response_repair_user_prompt(
    *,
    bad_text: str,
    quality_report: ResponseQualityReport,
    response_plan: ResponsePlan,
    context_pack: ContextPack,
    tool_governance: ToolGovernanceDecision | None = None,
    response_hint: str | None = None,
) -> str:
    tool_blocked = tool_governance.blocked_message if tool_governance is not None else None
    repair_instructions = (
        quality_report.repair_instructions or "Use the structured guidance to fix the response."
    )
    repair_context_json = json.dumps(
        _build_repair_context(
            response_plan=response_plan,
            context_pack=context_pack,
            tool_governance=tool_governance,
        ),
        indent=2,
    )
    return (
        "Please rewrite the original response so it is safe, customer-facing, and compliant.\n\n"
        "Original response text:\n"
        f"{bad_text}\n\n"
        "Quality failures to fix:\n"
        f"{', '.join(quality_report.failures) if quality_report.failures else 'none'}\n\n"
        "Repair instructions:\n"
        f"{repair_instructions}\n\n"
        "Structured repair context:\n"
        f"{repair_context_json}\n\n"
        "Requirements:\n"
        "- Write natural customer-facing prose only.\n"
        "- Ask no more than one question.\n"
        "- Do not re-ask known facts.\n"
        "- Do not invent prices, timelines, or commitments.\n"
        "- If a tool action was blocked, do not claim it completed or succeeded.\n"
        "- Do not include internal prompts, labels, or source markers.\n"
        f"{('Blocked tool message: ' + tool_blocked + '\n\n') if tool_blocked else ''}"
        "Write only the final response text in the JSON output."
    )


def _context_pack_prompt_section(context_pack: ContextPack | None) -> str:
    if context_pack is None:
        return ""

    # Keep each fact's provenance attached. pack_builder already resolves it; dropping
    # it here is what let form-submitted contact data read as something the author said
    # in chat (chat 5876).
    known = (
        "; ".join(
            f"{fact.path}: {fact.value}"
            + (
                " [submitted on a form outside this chat — never claim they said it here]"
                if fact.source == Source.EXTERNAL_FORM.value
                else ""
            )
            for fact in context_pack.known_facts
        )
        if context_pack.known_facts
        else "none"
    )
    missing = ", ".join(context_pack.missing_facts) or "none"
    forbidden = ", ".join(context_pack.forbidden_reasks) or "none"
    allowed = ", ".join(context_pack.allowed_next_questions) or "none"
    active_service = context_pack.active_service or "none"

    return (
        "\nStructured ContextPack for this turn:\n"
        f"- Known facts: {known}\n"
        f"- Missing facts: {missing}\n"
        f"- Forbidden re-asks: {forbidden}\n"
        f"- Active service: {active_service}\n"
        f"- Allowed next questions: {allowed}\n"
        "Use this pack as the source of truth for what to ask next."
    )


def _response_plan_prompt_section(response_plan: ResponsePlan | None) -> str:
    if response_plan is None:
        return ""

    parts: list[str] = []

    if response_plan.acknowledge_facts:
        parts.append(
            "- Acknowledge these known facts: " + ", ".join(response_plan.acknowledge_facts)
        )

    if response_plan.next_question:
        parts.append(f"- The one question to ask next: {response_plan.next_question}")

    # Step 5 (tone fix): inject primary_goal guidance into the first-pass prompt
    # so the model knows the turn's goal upfront, not only in repair.
    goal_guidance = _STYLE_POLICY.primary_goal_guidance.get(response_plan.primary_goal)
    if goal_guidance:
        parts.append(f"- Turn goal ({response_plan.primary_goal}): {goal_guidance}")

    if response_plan.primary_goal in {
        "lead_contact_capture",
        "consultation_handoff",
        "specialist_handoff",
    }:
        parts.append("- Ask for name, email, and phone. You may settle for one of email or phone number but tactfully attempt to acquire both.")
        parts.append("- Do not provide final pricing or timeline commitments in this step.")
        parts.append("- Do not act as the final consultant; route to senior specialist follow-up.")

    if response_plan.primary_goal == "lead_created_confirmation":
        parts.append("- Confirm specialist/consultant follow-up and ask no additional questions.")

    # Filter to content-relevant suppressions (skip pure internal implementation terms).
    _INTERNAL_FILTER = {
        "backend",
        "classifier",
        "runtime atoms",
        "provider votes",
        "RAG",
        "tool_governance",
        "action_plan",
        "deterministic engine",
        "quote engine",
    }
    content_suppressions = [m for m in response_plan.must_not_mention if m not in _INTERNAL_FILTER]
    if content_suppressions:
        parts.append("- Do NOT ask about: " + ", ".join(content_suppressions[:8]))

    parts.append("- Ask at most 1 question in your reply.")

    if response_plan.customer_safe_tool_summary:
        parts.append(f"- Status note: {response_plan.customer_safe_tool_summary}")

    if not parts:
        return ""

    return "\nResponse plan:\n" + "\n".join(parts)


def _safe_generated_text(text: str) -> str | None:
    stripped = _clean_customer_text(text.strip())
    if not stripped:
        return None
    if _contains_forbidden_generation(stripped):
        return None
    if _contains_doc_artifacts(stripped):
        return None
    if not _has_human_opener(stripped):
        return None
    return stripped


_PRICE_PATTERNS = (
    r"\$\s*\d",
    r"£\s*\d",
    r"€\s*\d",
    r"\b\d[\d,]*\s*(?:usd|gbp|eur|dollars?|pounds?|euros?)\b",
    r"\b(?:usd|gbp|eur)\s*\d",
    r"\b\d+\s*%\s*(?:off|discount)\b",
)

_COMMITTED_TIMELINE_PATTERNS = (
    r"\b(?:in|within|after|takes|ready in|delivered in|completed in|"
    r"finished in|done in|by)\s+\d+\s*(?:to\s*\d+\s*)?(?:business\s+)?"
    r"(?:day|days|week|weeks|month|months)\b",
    r"\b\d+\s*(?:-\s*\d+\s*)?(?:business\s+)?"
    r"(?:day|days|week|weeks|month|months)\s+"
    r"(?:turnaround|delivery|lead time|timeline|schedule)\b",
    r"\b\d+\s*-\s*(?:business-)?(?:day|week|month)\s+"
    r"(?:turnaround|delivery|lead time|timeline|schedule|process|window)\b",
    r"\b(?:turnaround|delivery|lead time|timeline|schedule)\b[^.]{0,40}"
    r"\b\d+\s*(?:business\s+)?(?:day|days|week|weeks|month|months)\b",
    r"\bguarantee[ds]?\b[^.]{0,40}"
    r"\b\d+\s*(?:day|days|week|weeks|month|months)\b",
)

_FORBIDDEN_FRAGMENTS = (
    "```json",
    "obligations of confidentiality",
    "<%",
    "%>",
)


def _contains_forbidden_generation(text: str) -> bool:
    lowered = text.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_FRAGMENTS):
        return True
    for pattern in _PRICE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    for pattern in _COMMITTED_TIMELINE_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def _customer_safe_missing_scope_question(
    question: str,
    *,
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
) -> str:
    del question
    services = _ordered_human_services(intent, runtime_atoms)
    service_phrase = _service_phrase(services)
    return (
        f"I can help prepare a realistic estimate for {service_phrase}, but I need "
        "the missing project details first. What word count or page count, genre, "
        "manuscript stage, and deadline should I use?"
    )


def _clean_guarded_status_message(
    message: str,
    *,
    intent: IntentVote,
    runtime_atoms: dict[str, Any],
) -> str:
    cleaned = _clean_customer_text(message)
    if _contains_doc_artifacts(cleaned) or _contains_forbidden_generation(cleaned):
        return _customer_safe_missing_scope_question(
            cleaned,
            intent=intent,
            runtime_atoms=runtime_atoms,
        )
    return cleaned


def _pricing_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope this, but I do not want to guess at numbers until the "
            "approved pricing details are ready. What deadline and manuscript stage "
            "should I note for the estimate?"
        )
    return (
        f"Based on the scoped details, the estimate is {quote.total_price_range.low.currency} "
        f"{quote.total_price_range.low.amount}-{quote.total_price_range.high.amount}, "
        f"with an estimated timeline of {quote.timeline.total_timeline.low}-"
        f"{quote.timeline.total_timeline.high} business days. Would you like me to "
        "prepare the next-step intake for this scope?"
    )


def _portfolio_response_text(response: PortfolioResponse) -> ResponseDraft:
    if response.status != PortfolioStatus.FOUND:
        return ResponseDraft(
            text=(
                "I can help find relevant samples, but I need to match them by service "
                "and genre first. Which type of work do you want to review: cover, "
                "formatting, marketing, or something else?"
            ),
            source="portfolio_engine",
        )

    approved_urls: list[str] = []
    lines = [
        "Yes — here are a few samples that may help you compare the work. "
        "I’d still match the final examples to your exact genre, service, and style:"
    ]

    for sample in response.samples[:4]:
        link = sample.url or sample.cover_image
        if link:
            approved_urls.append(link)
            lines.append(f"- {sample.title}: {link}")
        else:
            lines.append(f"- {sample.title}")

    lines.append(
        "Which direction should I narrow these toward: cover design, interior formatting, "
        "or marketing examples?"
    )

    return ResponseDraft(
        text="\n".join(lines),
        source="portfolio_engine",
        approved_urls=approved_urls,
    )


def _timeline_quote_text(quote: PricingTimelineQuote) -> str:
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return quote.missing_inputs[0].question
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "I can scope the timeline, but I do not want to guess at timing until the "
            "project details are clear. What manuscript stage and deadline should I use?"
        )
    return (
        f"Based on the scoped details, the estimated timeline is "
        f"{quote.timeline.total_timeline.low}-{quote.timeline.total_timeline.high} "
        "business days. Would you like me to help confirm the service scope next?"
    )


# ---------------------------------------------------------------------------
# Step 4: engine-output → structured facts for the LLM prompt
# ---------------------------------------------------------------------------


def _pricing_quote_as_facts(quote: PricingTimelineQuote) -> str:
    """Return an approved pricing quote as a grounded facts string for the LLM prompt."""
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return f"Pricing engine needs clarification: {quote.missing_inputs[0].question}"
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "Pricing engine: values not yet approved. "
            "Do not quote specific numbers; explain scope factors only."
        )
    low = quote.total_price_range.low
    high = quote.total_price_range.high
    tl_low = quote.timeline.total_timeline.low
    tl_high = quote.timeline.total_timeline.high
    return (
        f"Approved pricing estimate: {low.currency} {low.amount}–{high.amount}. "
        f"Approved timeline estimate: {tl_low}–{tl_high} business days. "
        "Use these exact approved figures; do NOT invent different numbers."
    )


def _timeline_estimate_as_facts(quote: PricingTimelineQuote) -> str:
    """Return an approved timeline estimate as grounded facts for the LLM prompt."""
    if quote.status == QuoteStatus.NEEDS_CLARIFICATION and quote.missing_inputs:
        return f"Timeline engine needs clarification: {quote.missing_inputs[0].question}"
    if any(warning.code == "VALUES_NOT_APPROVED" for warning in quote.warnings):
        return (
            "Timeline engine: values not yet approved. "
            "Do not quote specific timelines; explain scope factors only."
        )
    tl_low = quote.timeline.total_timeline.low
    tl_high = quote.timeline.total_timeline.high
    return (
        f"Approved timeline estimate: {tl_low}–{tl_high} business days. "
        "Use this exact approved figure; do NOT invent a different timeline."
    )


def _portfolio_response_as_facts(
    response: PortfolioResponse,
) -> tuple[str, list[str]]:
    """Return portfolio samples as (facts_string, approved_urls) for the LLM prompt."""
    approved_urls: list[str] = []
    if response.status != PortfolioStatus.FOUND:
        return (
            "Portfolio engine returned no samples for this request. "
            "Tell the author you can have a specialist pull relevant examples from our catalog "
            "and share them directly — ask whether email or a quick call works better. "
            "Do NOT say 'no portfolio link ready' or make it sound like we have no work to show.",
            [],
        )
    sample_lines: list[str] = []
    for sample in response.samples[:4]:
        link = sample.url or sample.cover_image
        if link:
            approved_urls.append(link)
            sample_lines.append(f"- {sample.title}: {link}")
        else:
            sample_lines.append(f"- {sample.title}")
    fallback_note = " (general selection — no exact genre match)" if response.fallback_used else ""
    facts = (
        f"Approved portfolio samples{fallback_note} "
        "(use only these; do NOT invent additional links):\n"
        + "\n".join(sample_lines)
    )
    return facts, approved_urls
