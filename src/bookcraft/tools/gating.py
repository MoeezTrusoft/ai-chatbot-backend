from dataclasses import dataclass
from typing import Literal

from bookcraft.domain.enums import ToolClass

DocumentMode = Literal["manual", "verifier_gated", "autonomous"]


@dataclass(frozen=True, slots=True)
class GatingDecision:
    allowed: bool
    deferred: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ToolGatingPolicy:
    nda_mode: DocumentMode = "manual"
    agreement_mode: DocumentMode = "manual"

    def decide(self, *, tool_name: str, tool_class: ToolClass) -> GatingDecision:
        if tool_class != ToolClass.HIGH_STAKES_DOCUMENT:
            return GatingDecision(allowed=True)

        mode = self._document_mode(tool_name)
        if mode == "manual":
            return GatingDecision(
                allowed=False,
                deferred=True,
                reason=f"{tool_name} deferred because document mode is manual.",
            )
        return GatingDecision(allowed=True)

    def _document_mode(self, tool_name: str) -> DocumentMode:
        normalized = tool_name.lower()
        if "nda" in normalized:
            return self.nda_mode
        if "agreement" in normalized:
            return self.agreement_mode
        return "manual"

