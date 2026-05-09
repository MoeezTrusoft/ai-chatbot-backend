from pydantic import BaseModel, ConfigDict, Field


class ResponseRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    reason: str
    requires_tool_output: bool = False


class ResponseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source: str
    approved_urls: list[str] = Field(default_factory=list)


class GeneratedResponseText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=3000)


class FormattedBubble(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    bubble_index: int
    rich_segments: list[dict[str, str]] = Field(default_factory=list)
