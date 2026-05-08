from __future__ import annotations

from bookcraft.components.documents.engine import DocumentEngine
from bookcraft.components.documents.schemas import (
    AgreementParams,
    DocumentToolInput,
    DocumentToolOutput,
    NDAParams,
)
from bookcraft.domain.enums import ToolClass
from bookcraft.tools import ToolContext, ToolDefinition, ToolRegistry


def register_document_tools(registry: ToolRegistry, engine: DocumentEngine) -> None:
    async def generate_nda(
        input_data: DocumentToolInput,
        context: ToolContext,
    ) -> DocumentToolOutput:
        del context
        params = NDAParams.model_validate(input_data.params)
        return DocumentToolOutput(document=engine.generate_nda(params))

    async def generate_agreement(
        input_data: DocumentToolInput,
        context: ToolContext,
    ) -> DocumentToolOutput:
        del context
        params = AgreementParams.model_validate(input_data.params)
        return DocumentToolOutput(document=engine.generate_agreement(params))

    registry.register(
        ToolDefinition(
            name="documents.generate_nda.v1",
            tool_class=ToolClass.HIGH_STAKES_DOCUMENT,
            input_model=DocumentToolInput,
            output_model=DocumentToolOutput,
            handler=generate_nda,
            timeout_seconds=10.0,
        )
    )
    registry.register(
        ToolDefinition(
            name="documents.generate_agreement.v1",
            tool_class=ToolClass.HIGH_STAKES_DOCUMENT,
            input_model=DocumentToolInput,
            output_model=DocumentToolOutput,
            handler=generate_agreement,
            timeout_seconds=10.0,
        )
    )
