from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DEV_APP_ENVS = frozenset("test dev development local".split())


class CustomerResponseContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_responder: Literal["claude_required"] = "claude_required"
    allow_deterministic_customer_text: bool = False
    allow_deterministic_structured_payload: bool = True
    allow_claude_repair: bool = True
    allowed_final_sources: tuple[str, ...] = ("claude_sonnet", "claude_sonnet_repair")
    dev_allowed_sources: tuple[str, ...] = ("template_no_adapter", "deterministic_greeting")

    def _is_production_like(self, app_env: str | None = None) -> bool:
        if app_env is None:
            return True
        return app_env not in DEV_APP_ENVS

    def is_allowed_final_source(self, source: str, *, app_env: str | None = None) -> bool:
        if self._is_production_like(app_env):
            return source in self.allowed_final_sources
        return (
            source in self.allowed_final_sources
            or source in self.dev_allowed_sources
            or source.endswith("_quality_fallback")
        )

    def requires_claude(self, source: str, *, app_env: str | None = None) -> bool:
        if self._is_production_like(app_env):
            return source in self.allowed_final_sources
        return source in self.allowed_final_sources
