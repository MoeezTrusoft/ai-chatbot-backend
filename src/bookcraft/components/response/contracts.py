from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DEV_APP_ENVS = frozenset("test dev development local".split())

# Sources that are production-compliant Claude responses.
_PRODUCTION_COMPLIANT_SOURCES: frozenset[str] = frozenset({"claude_sonnet", "claude_sonnet_repair"})

# Prefixes that indicate a deterministic / non-Claude final response.
_DETERMINISTIC_PREFIXES: tuple[str, ...] = (
    "template_",
    "deterministic_",
    "clarification_",
    "portfolio_engine_",
)


def is_production_like(app_env: str | None) -> bool:
    """Return True when the runtime environment is production-like."""
    if app_env is None:
        return True
    return app_env not in DEV_APP_ENVS


def is_deterministic_source(source: str) -> bool:
    """Return True when the source is known to be deterministic / non-Claude."""
    if not source:
        return False
    for prefix in _DETERMINISTIC_PREFIXES:
        if source.startswith(prefix):
            return True
    # Any _quality_fallback that is not the Claude repair path is deterministic.
    if source.endswith("_quality_fallback") and source not in _PRODUCTION_COMPLIANT_SOURCES:
        return True
    return False


def is_production_compliant_source(source: str) -> bool:
    """Return True only for sources that are production-safe Claude responses."""
    return source in _PRODUCTION_COMPLIANT_SOURCES


class CustomerResponseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_responder: Literal["claude_required"] = "claude_required"
    allow_deterministic_customer_text: bool = False
    allow_deterministic_structured_payload: bool = True
    allow_claude_repair: bool = True
    allowed_final_sources: tuple[str, ...] = (
        "claude_sonnet",
        "claude_sonnet_repair",
    )
    dev_allowed_sources: tuple[str, ...] = ("template_no_adapter", "deterministic_greeting")

    def _is_production_like(self, app_env: str | None = None) -> bool:
        return is_production_like(app_env)

    def is_allowed_final_source(self, source: str, *, app_env: str | None = None) -> bool:
        """Return True when the source is permitted as a final response in this env."""
        if self._is_production_like(app_env):
            return source in self.allowed_final_sources
        return (
            source in self.allowed_final_sources
            or source in self.dev_allowed_sources
            or source.endswith("_quality_fallback")
        )

    def is_production_compliant_source(self, source: str) -> bool:
        """Return True only for production-safe Claude sources."""
        return is_production_compliant_source(source)

    def is_deterministic_source(self, source: str) -> bool:
        """Return True when the source is deterministic / non-Claude."""
        return is_deterministic_source(source)

    def requires_claude(self, source: str, *, app_env: str | None = None) -> bool:
        if self._is_production_like(app_env):
            return source in self.allowed_final_sources
        return source in self.allowed_final_sources
